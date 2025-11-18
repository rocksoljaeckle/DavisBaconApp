import agents
from agents import *
from typing import Optional
from PIL import Image
import pytesseract
import io
from anthropic import AsyncAnthropic
from pypdf import PdfReader, PdfWriter
import pypdfium2 as pdfium
from openai import AsyncOpenAI
import json
import asyncio
from pydantic import BaseModel
from rapidfuzz import fuzz
from rapidfuzz.utils import default_process as rapidfuzz_default_process
import tomli
import base64
import googlemaps
import fitz

from GlobalUtils.ocr import whisper_pdf_text_extraction
from GlobalUtils.openai_uploading import get_or_upload_async
from GlobalUtils.citation import find_best_openai_lines, render_line_highlights


class EmployeeWageCheck(BaseModel):
    employee_name: str
    title: str
    davis_bacon_classification: str
    davis_bacon_base_rate: float
    davis_bacon_fringe_rate: float
    davis_bacon_total_rate: float
    overtime_rate: Optional[float]
    paid_rate: float
    compliance_reasoning: str
    compliance: str

class ComplianceTable(BaseModel):
    payroll_name: str
    wage_checks: list[EmployeeWageCheck]

@function_tool
def report_compliance_table(compliance_table: ComplianceTable):
    """Report the compliance table for a payroll."""
    return compliance_table

@function_tool
def report_wage_check(wage_check: EmployeeWageCheck):
    """Report an employee wage check."""
    return wage_check

@function_tool
def report_parsing_error(error_message: str):
    """Report an error in parsing the compliance table."""
    return error_message

def render_pdf_page_to_image(pdf, page_index: int, dpi: int) -> Image.Image:
    page = pdf.get_page(page_index)
    # scale factor: 72 pts per inch base
    scale = dpi / 72.0
    bitmap = page.render(scale=scale)
    pil_img = bitmap.to_pil()
    page.close()
    bitmap.close()
    return pil_img


def ocr_image_to_pdf_bytes(img: Image.Image, lang: str, oem: Optional[int], psm: Optional[int], tesseract_config = '') -> bytes:
    config_parts = []
    if oem is not None:
        config_parts.append(f"--oem {oem}")
    if psm is not None:
        config_parts.append(f"--psm {psm}")
    config = " ".join(config_parts) if config_parts else None
    # returns a complete single-page PDF (bytes) with an invisible text layer
    return pytesseract.image_to_pdf_or_hocr(img, lang=lang, extension="pdf", config=tesseract_config)


def merge_pdf_pages(pdf_pages: list[bytes], out_path: str) -> None:
    writer = PdfWriter()
    for idx, page_bytes in enumerate(pdf_pages):
        reader = PdfReader(io.BytesIO(page_bytes))
        # sanity: expect exactly one page per chunk
        if len(reader.pages) == 0:
            continue
        writer.add_page(reader.pages[0])
    with open(out_path, "wb") as f:
        writer.write(f)

def ocr_pdf_text_overlay(input_pdf_path: str, output_pdf_path: str, lang: str = "eng", dpi: int  = 300, tesseract_config = ''):
    pdf = pdfium.PdfDocument(input_pdf_path)
    num_pages = len(pdf)
    ocred_pages = []
    for page_index in range(num_pages):
        print(f"OCRing page {page_index+1}/{num_pages}...")
        img = render_pdf_page_to_image(pdf, page_index, dpi)
        ocred_pdf_bytes = ocr_image_to_pdf_bytes(img, lang, oem=1, psm=3, tesseract_config = tesseract_config)
        ocred_pages.append(ocred_pdf_bytes)
    pdf.close()
    print(f"Merging {len(ocred_pages)} pages into output PDF...")
    merge_pdf_pages(ocred_pages, output_pdf_path)
    print(f"Done. Output written to {output_pdf_path}")

def get_lines_page_numbers(lines: list[int], page_lengths: list[int]) -> list[int]:
    '''Get the pages corresponding to the given line numbers.

    Returns a dict mapping page # to the lines to highlight on that page.'''
    lines = sorted(lines)
    pages = dict()
    lines_ind = 0
    cumulative_lines = 0
    for page_index, page_length in enumerate(page_lengths):
        while lines_ind < len(lines):
            if lines[lines_ind] < cumulative_lines + page_length:
                if page_index not in pages:
                    pages[page_index] = []
                pages[page_index].append(lines[lines_ind]-cumulative_lines)
                lines_ind += 1
            else:
                break
        cumulative_lines += page_length
    return pages

async def get_db_wages_citation_images(
        db_wages_file_path: str,
        db_wages_citation_query: str,
        citation_prompt: str,
        openai_client: AsyncOpenAI
):
    page_lengths = []
    db_wages_doc = fitz.open(db_wages_file_path)
    db_wages_file_text = ''
    for page in db_wages_doc:
        page_text = page.get_text().strip()
        db_wages_file_text += page_text + '\n'
        page_lengths.append(page_text.count('\n')+1)
    citation_lines = await find_best_openai_lines(
        text=db_wages_file_text,
        query=db_wages_citation_query,
        citation_prompt=citation_prompt,
        openai_client=openai_client,
    )
    print(f'openai lines: {citation_lines}')
    print('\n'.join([db_wages_file_text.splitlines()[i] for i in citation_lines]))
    citation_pages_dict = get_lines_page_numbers(citation_lines, page_lengths)

    citation_pages = []
    citation_images = []
    for page, lines in citation_pages_dict.items():
        citation_pages.append(page)
        citation_images.append(
            render_line_highlights(
                text = db_wages_doc[page].get_text().strip(),
                highlight_lines = lines
            )
        )
    db_wages_doc.close()

    return citation_images, citation_pages

async def openai_payroll_compliance_table(
        openai_client: AsyncOpenAI,
        openai_model: str,
        openai_compliance_matrix_prompt: str,
        db_wages_file_path: str,
        payroll_file_path: str,
        openai_files_cache_path: str,
        payroll_ocr_str: str|None = None,
        project_location_str: str|None = None
):
    # openai extraction
    upload_coroutines = [
        get_or_upload_async(
            file_path=path,
            client=openai_client,
            cache_path=openai_files_cache_path,
            purpose='user_data'
        )
        for path in [db_wages_file_path, payroll_file_path]
    ]
    db_wages_file_id, payroll_file_id = await asyncio.gather(*upload_coroutines)
    openai_compliance_agent = Agent(
        name="Payroll Compliance Agent",
        instructions=openai_compliance_matrix_prompt,
        tools=[report_compliance_table, report_parsing_error],
        model=openai_model,
        tool_use_behavior='stop_on_first_tool'
    )
    openai_compliance_input = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'input_file',
                    'file_id': db_wages_file_id
                },
                {
                    'type': 'input_file',
                    'file_id': payroll_file_id
                }
            ]
        }
    ]
    if payroll_ocr_str is not None:
        openai_compliance_input[0]['content'].append({
            'type': 'input_text',
            'text': 'The following was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + payroll_ocr_str
        })
    if project_location_str is not None:
        openai_compliance_input[0]['content'].append({
            'type': 'input_text',
            'text': 'The project is located in the following county/location: ' + project_location_str
        })
    with trace('Payroll Compliance Workflow'):
        openai_compliance_result = await Runner.run(openai_compliance_agent, input=openai_compliance_input)
    for i, item in enumerate(openai_compliance_result.new_items):
        if (
                i > 0 and
                isinstance(item, agents.items.ToolCallOutputItem) and
                isinstance(openai_compliance_result.new_items[i - 1], agents.items.ToolCallItem) and
                openai_compliance_result.new_items[i - 1].raw_item.name == 'report_compliance_table'
        ):
            openai_compliance_table = item.output
            break
    else:
        openai_compliance_table = None
    return openai_compliance_table

async def claude_payroll_compliance_table(
        anthropic_client: AsyncAnthropic,
        claude_model: str,
        claude_compliance_matrix_prompt: str,
        db_wages_file_path: str,
        payroll_file_path: str,
        payroll_ocr_str: str|None = None,
        project_location_str: str|None = None
):
    with open(payroll_file_path, "rb") as f:
        payroll_bytes = f.read()
    payroll_base64_string = base64.b64encode(payroll_bytes).decode('utf-8')
    with open(db_wages_file_path, "rb") as f:
        db_wages_bytes = f.read()
    db_wages_base64_string = base64.b64encode(db_wages_bytes).decode('utf-8')
    claude_compliance_input = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'text',
                    'text': claude_compliance_matrix_prompt  # todo -special claude prompt
                },
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': db_wages_base64_string
                    }
                },
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': payroll_base64_string
                    }
                }
            ]
        },
        {
            'role': 'assistant',
            'content': '{"success":'
        }
    ]
    if payroll_ocr_str is not None:
        claude_compliance_input[0]['content'].append({
            'type': 'text',
            'text': 'The following was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + payroll_ocr_str
        })
    if project_location_str is not None:
        claude_compliance_input[0]['content'].append({
            'type': 'text',
            'text': 'The project is located in the following county/location: ' + project_location_str
        })
    claude_compliance_response = await anthropic_client.messages.create(
        model=claude_model,
        messages=claude_compliance_input,
        max_tokens=10_000
    )

    claude_compliance_result = json.loads('{"success":' + claude_compliance_response.content[0].text)
    if claude_compliance_result.get('success'):
        if 'wage_checks' not in claude_compliance_result:
            raise ValueError("Claude response indicates success but no 'wage_checks' found in response.")
        claude_wage_checks = [
            EmployeeWageCheck(
                employee_name=wage_check['employee_name'],
                title=wage_check['title'],
                davis_bacon_classification=wage_check['davis_bacon_classification'],
                davis_bacon_base_rate=wage_check['davis_bacon_base_rate'],
                davis_bacon_fringe_rate=wage_check['davis_bacon_fringe_rate'],
                davis_bacon_total_rate=wage_check['davis_bacon_total_rate'],
                overtime_rate=wage_check.get('overtime_rate'),
                paid_rate=wage_check['paid_rate'],
                compliance_reasoning=wage_check['compliance_reasoning'],
                compliance=wage_check['compliance']
            )
            for wage_check in claude_compliance_result['wage_checks']
        ]
        claude_compliance_table = ComplianceTable(
            payroll_name=claude_compliance_result.get('payroll_name', ''),
            wage_checks=claude_wage_checks
        )
    else:
        print('Claude failed to extract compliance table:', json.dumps(claude_compliance_result,indent=2)) # todo remove
        claude_compliance_table = None
    return claude_compliance_table

async def openai_single_wage_check(
        openai_wc: EmployeeWageCheck,
        openai_client: AsyncOpenAI,
        openai_model: str,
        openai_single_wage_check_prompt: str,
        db_wages_file_path: str,
        payroll_file_path: str,
        openai_files_cache_path: str,
        payroll_ocr_str: str|None = None,
        project_location_str: str|None = None
):
    # openai extraction
    upload_coroutines = [
        get_or_upload_async(
            file_path=path,
            client=openai_client,
            cache_path=openai_files_cache_path,
            purpose='user_data'
        )
        for path in [db_wages_file_path, payroll_file_path]
    ]
    db_wages_file_id, payroll_file_id = await asyncio.gather(*upload_coroutines)
    openai_check_agent = Agent(
        name="Payroll Check Agent",
        instructions=openai_single_wage_check_prompt,
        tools=[report_wage_check, report_parsing_error],
        model=openai_model,
        tool_use_behavior='stop_on_first_tool'
    )
    openai_check_input = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'input_file',
                    'file_id': db_wages_file_id
                },
                {
                    'type': 'input_file',
                    'file_id': payroll_file_id
                }
            ]
        }
    ]
    if payroll_ocr_str is not None:
        openai_check_input[0]['content'].append({
            'type': 'input_text',
            'text': 'The following text was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + payroll_ocr_str
        })
    if project_location_str is not None:
        openai_check_input[0]['content'].append({
            'type': 'input_text',
            'text': 'The project is located in the following county/location: ' + project_location_str
        })
    openai_check_input[0]['content'].append({
        'type': 'input_text',
        'text': f'Please extract the payroll information for the following employee: {openai_wc.employee_name}'
    })
    with trace(f'Payroll Checking Workflow for {openai_wc.employee_name}'):
        openai_check_result = await Runner.run(openai_check_agent, input=openai_check_input)
    for i, item in enumerate(openai_check_result.new_items):
        if (
                i > 0 and
                isinstance(item, agents.items.ToolCallOutputItem) and
                isinstance(openai_check_result.new_items[i - 1], agents.items.ToolCallItem) and
                openai_check_result.new_items[i - 1].raw_item.name == 'report_wage_check'
        ):
            openai_wage_check = item.output
            break
    else:
        openai_wage_check = None
    return openai_wage_check

async def claude_single_wage_check(
        claude_wc: EmployeeWageCheck,
        anthropic_client: AsyncAnthropic,
        claude_model: str,
        claude_single_wage_check_prompt: str,
        db_wages_file_path: str,
        payroll_file_path: str,
        payroll_ocr_str: str|None = None,
        project_location_str: str|None = None
):
    with open(payroll_file_path, "rb") as f:
        payroll_bytes = f.read()
    payroll_base64_string = base64.b64encode(payroll_bytes).decode('utf-8')
    with open(db_wages_file_path, "rb") as f:
        db_wages_bytes = f.read()
    db_wages_base64_string = base64.b64encode(db_wages_bytes).decode('utf-8')
    claude_check_input = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'text',
                    'text': claude_single_wage_check_prompt
                },
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': db_wages_base64_string
                    }
                },
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': payroll_base64_string
                    }
                }
            ]
        },
        {
            'role': 'assistant',
            'content': '{"success":'
        }
    ]
    if payroll_ocr_str is not None:
        claude_check_input[0]['content'].append({
            'type': 'text',
            'text': 'The following text was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + payroll_ocr_str
        })
    if project_location_str is not None:
        claude_check_input[0]['content'].append({
            'type': 'text',
            'text': 'The project is located in the following county/location: ' + project_location_str
        })
    claude_check_input[0]['content'].append({
        'type': 'text',
        'text': f'Please extract the payroll information for the following employee: {claude_wc.employee_name}'
    })

    claude_check_response = await anthropic_client.messages.create(
        model=claude_model,
        messages=claude_check_input,
        max_tokens=10_000
    )

    new_wage_check = json.loads('{"success":' + claude_check_response.content[0].text)
    if new_wage_check.get('success'):
        claude_wage_check = EmployeeWageCheck(
            employee_name=new_wage_check['employee_name'],
            title=new_wage_check['title'],
            davis_bacon_classification=new_wage_check['davis_bacon_classification'],
            davis_bacon_base_rate=new_wage_check['davis_bacon_base_rate'],
            davis_bacon_fringe_rate=new_wage_check['davis_bacon_fringe_rate'],
            davis_bacon_total_rate=new_wage_check['davis_bacon_total_rate'],
            overtime_rate=new_wage_check.get('overtime_rate'),
            paid_rate=new_wage_check['paid_rate'],
            compliance_reasoning=new_wage_check['compliance_reasoning'],
            compliance=new_wage_check['compliance']
        )
    else:
        claude_wage_check = None # ehhh not sure if i love this
    return claude_wage_check

async def resolve_disputed_check(
        openai_wc: EmployeeWageCheck,
        claude_wc: EmployeeWageCheck,
        openai_client: AsyncOpenAI,
        openai_model: str,
        openai_single_wage_check_prompt: str,
        anthropic_client: AsyncAnthropic,
        claude_model: str,
        claude_single_wage_check_prompt: str,
        db_wages_file_path: str,
        payroll_file_path: str,
        openai_files_cache_path: str,
        payroll_ocr_str: str|None = None,
        project_location_str: str|None = None
) -> Optional[EmployeeWageCheck]:
    openai_check, claude_check = await asyncio.gather(
        openai_single_wage_check(
            openai_wc = openai_wc,
            openai_client = openai_client,
            openai_model = openai_model,
            openai_single_wage_check_prompt = openai_single_wage_check_prompt,
            db_wages_file_path = db_wages_file_path,
            openai_files_cache_path=openai_files_cache_path,
            payroll_file_path = payroll_file_path,
            project_location_str = project_location_str
        ),
        claude_single_wage_check(
            claude_wc = claude_wc,
            anthropic_client = anthropic_client,
            claude_model = claude_model,
            claude_single_wage_check_prompt = claude_single_wage_check_prompt,
            db_wages_file_path = db_wages_file_path,
            payroll_file_path = payroll_file_path,
            payroll_ocr_str = payroll_ocr_str,
            project_location_str = project_location_str
        )
    )
    if openai_check is None and claude_check is None:
        return None
    elif openai_check is None:
        return claude_check
    elif claude_check is None:
        return openai_check
    elif abs(openai_wc.davis_bacon_total_rate - claude_wc.davis_bacon_total_rate) > 0.1:
        return None
    elif abs(openai_wc.paid_rate - claude_wc.paid_rate) > 0.1:
        return None
    else:  # why do we throw away everything else? because its harder to match those strings. there may be idiosyncrasies in naming conventions
        return claude_wc  # prefer claude


async def get_payroll_compliance_table(
        openai_client: AsyncOpenAI,
        openai_model: str,
        openai_compliance_matrix_prompt: str,
        openai_single_wage_check_prompt: str,
        anthropic_client: AsyncAnthropic,
        claude_model: str,
        claude_compliance_matrix_prompt: str,
        claude_single_wage_check_prompt: str,
        db_wages_file_path: str,
        payroll_file_path: str,
        openai_files_cache_path: str,
        payroll_ocr_str: str|None = None,
        project_location_str: str|None = None,
        name_match_threshold: float = 80.
):
    '''Get the payroll compliance table directly from the payroll and DB wages files (no OCR).'''
    openai_compliance_task = openai_payroll_compliance_table(
        openai_client = openai_client,
        openai_model = openai_model,
        openai_compliance_matrix_prompt = openai_compliance_matrix_prompt,
        db_wages_file_path = db_wages_file_path,
        payroll_file_path = payroll_file_path,
        openai_files_cache_path = openai_files_cache_path,
        payroll_ocr_str = payroll_ocr_str,
        project_location_str = project_location_str
    )
    claude_compliance_task = claude_payroll_compliance_table(
        anthropic_client = anthropic_client,
        claude_model = claude_model,
        claude_compliance_matrix_prompt = claude_compliance_matrix_prompt,
        db_wages_file_path = db_wages_file_path,
        payroll_file_path = payroll_file_path,
        payroll_ocr_str = payroll_ocr_str,
        project_location_str = project_location_str
    )
    print('Getting compliance tables from OpenAI and Claude...')
    openai_compliance_table, claude_compliance_table = await asyncio.gather(
        openai_compliance_task,
        claude_compliance_task
    )
    print('Done.')
    if openai_compliance_table is None and claude_compliance_table is None:
        return None, None, None, None
    elif openai_compliance_table is None:
        return claude_compliance_table, None, None, None
    elif claude_compliance_table is None:
        return openai_compliance_table, None, None, None
    # if we reach here, both are non-null - concordance time
    wage_check_comparisons = []
    for openai_ind, openai_wc in enumerate(openai_compliance_table.wage_checks):
        for claude_ind, claude_wc in enumerate(claude_compliance_table.wage_checks):
            score = fuzz.ratio(openai_wc.employee_name, claude_wc.employee_name, processor=rapidfuzz_default_process)
            wage_check_comparisons.append((score, openai_ind, claude_ind))
    wage_check_comparisons.sort(reverse=True, key=lambda x: x[0])
    unmatched_openai_inds = set(range(len(openai_compliance_table.wage_checks)))
    unmatched_claude_inds = set(range(len(claude_compliance_table.wage_checks)))
    matched_wage_checks = []
    disputed_wage_checks = []
    for score, openai_ind, claude_ind in wage_check_comparisons:
        if score < name_match_threshold:
            break
        if (openai_ind not in unmatched_openai_inds) or (claude_ind not in unmatched_claude_inds):
            continue
        unmatched_openai_inds.remove(openai_ind)
        unmatched_claude_inds.remove(claude_ind)
        openai_wc = openai_compliance_table.wage_checks[openai_ind]
        claude_wc = claude_compliance_table.wage_checks[claude_ind]
        if abs(openai_wc.davis_bacon_total_rate - claude_wc.davis_bacon_total_rate)>0.1:
            disputed_wage_checks.append((openai_wc, claude_wc))
        elif abs(openai_wc.paid_rate - claude_wc.paid_rate)>0.1:
            disputed_wage_checks.append((openai_wc, claude_wc))
        else: # why do we throw away everything else? because its harder to match those strings. there may be idiosyncrasies in naming conventions
            matched_wage_checks.append(claude_wc) # prefer claude
    unmatched_openai = [openai_compliance_table.wage_checks[ind] for ind in unmatched_openai_inds]
    unmatched_claude = [claude_compliance_table.wage_checks[ind] for ind in unmatched_claude_inds]
    print('Resolving disputed wage checks...')
    disputed_resolution_tasks = [
        resolve_disputed_check(
            openai_wc = openai_wc,
            claude_wc = claude_wc,
            openai_client = openai_client,
            openai_model = openai_model,
            openai_single_wage_check_prompt = openai_single_wage_check_prompt,
            anthropic_client = anthropic_client,
            claude_model = claude_model,
            claude_single_wage_check_prompt = claude_single_wage_check_prompt,
            db_wages_file_path = db_wages_file_path,
            payroll_file_path = payroll_file_path,
            openai_files_cache_path = openai_files_cache_path,
            payroll_ocr_str = payroll_ocr_str,
            project_location_str = project_location_str
        )
        for openai_wc, claude_wc in disputed_wage_checks
    ]
    disputed_resolutions = await asyncio.gather(*disputed_resolution_tasks)
    agreed_wage_checks = [wage_check for wage_check in disputed_resolutions if wage_check is not None]
    disputed_wage_checks = [disputed_wage_checks[disputed_ind] for disputed_ind in range(len(disputed_wage_checks)) if disputed_resolutions[disputed_ind] is None]
    matched_wage_checks.extend(agreed_wage_checks)
    print('Done.')
    return (
        ComplianceTable(
            payroll_name = openai_compliance_table.payroll_name,
            wage_checks = matched_wage_checks
        ),
        disputed_wage_checks,
        unmatched_openai,
        unmatched_claude
    )

def create_search_location_tool(google_api_key: str):
    google_maps_client = googlemaps.Client(key=google_api_key)
    @function_tool
    def search_location(location_query: str):
        '''Search for a location using the Google Maps Geocoding API.'''
        geocode_result = google_maps_client.geocode(location_query)
        return json.dumps(geocode_result)
    return search_location

async def get_project_location(
        project_location_prompt: str,
        payroll_file_path: str,
        db_wages_file_path: str,
        gcloud_api_key: str,
        openai_files_cache_path: str,
        openai_client: AsyncOpenAI,
        openai_model: str = 'gpt-5',
        payroll_ocr_str: str|None = None
):
    search_location = create_search_location_tool(gcloud_api_key)
    location_agent = Agent(
        name="Project Location Extraction Agent",
        instructions=project_location_prompt,
        tools = [search_location],
        model = openai_model
    )
    upload_coroutines = [
        get_or_upload_async(
            file_path=path,
            client=openai_client,
            cache_path=openai_files_cache_path,
            purpose='user_data'
        )
        for path in [payroll_file_path, db_wages_file_path]
    ]
    payroll_file_id, db_wages_file_id = await asyncio.gather(*upload_coroutines)
    location_input = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'input_file',
                    'file_id': payroll_file_id
                },
                {
                    'type': 'input_file',
                    'file_id': db_wages_file_id
                }
            ]
        }
    ]

    if payroll_ocr_str is not None:
        location_input[0]['content'].append({
            'type': 'input_text',
            'text': 'The following text was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + payroll_ocr_str
        })
    with trace('Project Location Extraction Workflow'):
        location_result = await Runner.run(
            location_agent,
            input=location_input
        )
    return location_result.final_output


if __name__ == '__main__':
    with open('../GlobalUtils/config.toml', 'rb') as f:
        global_config = tomli.load(f)
    openai_api_key = global_config['openai_api_key']
    openai_client = AsyncOpenAI(api_key=openai_api_key)
    unstract_api_key = global_config['unstract_api_key']
    anthropic_api_key = global_config['anthropic_api_key']
    anthropic_client = AsyncAnthropic(api_key=anthropic_api_key)
    gcloud_api_key = global_config['gcloud_api_key']
    with open('../ProposalWriting/ddl_config.toml', 'rb') as f:
        ddl_config = tomli.load(f)
    openai_files_cache_path = ddl_config['openai_files_cache_path']
    with open('config.toml', 'rb') as f:
        config = tomli.load(f)
    with open(config['openai_compliance_matrix_prompt_path'], 'r') as f:
        openai_compliance_matrix_prompt = f.read()
    with open(config['openai_single_wage_check_prompt_path'], 'r') as f:
        openai_single_wage_check_prompt = f.read()
    with open(config['claude_compliance_matrix_prompt_path'], 'r') as f:
        claude_compliance_matrix_prompt = f.read()
    with open(config['claude_single_wage_check_prompt_path'], 'r') as f:
        claude_single_wage_check_prompt = f.read()
    with open(config['project_location_prompt_path'], 'r') as f:
        project_location_prompt = f.read()
    openai_model = config['openai_model']
    claude_model = config['claude_model']

    db_wages_file_path = r'C:\Users\jaeckle\PycharmProjects\DavisBaconApp\documents\rates.pdf'
    payroll_file_path = r'C:\Users\jaeckle\PycharmProjects\DavisBaconApp\documents\coulson.pdf'
    print('starting payroll compliance check...')
    set_default_openai_key(openai_api_key)
    print('running ocr...')
    payroll_ocr_str = whisper_pdf_text_extraction(
        unstract_api_key = unstract_api_key,
        input_pdf_path = payroll_file_path,
        max_retry_time = 300.,
        wait_step = 5.,
        max_wait_time = 300.,
    )
    print('ocr complete')

    print('Getting project county...')
    project_location_str = asyncio.run(get_project_location(
        payroll_file_path,
        project_location_prompt,
        db_wages_file_path,
        gcloud_api_key,
        openai_files_cache_path,
        openai_client,
        openai_model,
        payroll_ocr_str
    ))
    print(f'Project location extraction complete: "{project_location_str}"')

    compliance_table, disputed_wage_checks, unmatched_openai, unmatched_claude = asyncio.run(
        get_payroll_compliance_table(
            openai_client = openai_client,
            openai_model = openai_model,
            openai_compliance_matrix_prompt = openai_compliance_matrix_prompt,
            openai_single_wage_check_prompt = openai_single_wage_check_prompt,
            anthropic_client = anthropic_client,
            claude_model = claude_model,
            claude_compliance_matrix_prompt = claude_compliance_matrix_prompt,
            claude_single_wage_check_prompt = claude_single_wage_check_prompt,
            db_wages_file_path = db_wages_file_path,
            payroll_file_path = payroll_file_path,
            openai_files_cache_path = openai_files_cache_path,
            payroll_ocr_str = payroll_ocr_str,
            project_location_str = project_location_str,
            name_match_threshold = 80.
        )
    )
    pass