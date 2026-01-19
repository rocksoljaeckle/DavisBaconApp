import agents
import anthropic
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

from GlobalUtils.ocr import async_whisper_pdf_text_extraction
from GlobalUtils.openai_uploading import get_or_upload_async
from GlobalUtils.citation import (
    find_best_openai_lines,
    render_line_highlights,
    render_pdf_bboxes_to_images
)


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
    payroll_citation_lines: list[str]
    wage_determination_citation_lines: list[str]


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


def get_lines_page_numbers(lines: list[int], page_lengths: list[int]) -> dict[int, list[int]]:
    """Get the pages corresponding to the given line numbers.

    Returns a dict mapping page # to the lines to highlight on that page."""
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









def create_search_location_tool(google_api_key: str):
    google_maps_client = googlemaps.Client(key=google_api_key)
    @function_tool
    def search_location(location_query: str):
        '''Search for a location using the Google Maps Geocoding API.'''
        geocode_result = google_maps_client.geocode(location_query)
        return json.dumps(geocode_result)
    return search_location

class ComplianceChecker:
    def __init__(
            self,
            semaphore: asyncio.Semaphore,
            db_wages_file_path: str,
            payroll_file_path: str,
            openai_compliance_matrix_prompt: str, openai_single_wage_check_prompt: str,
            claude_compliance_matrix_prompt: str, claude_single_wage_check_prompt: str,
            project_location_prompt: str,
            openai_api_key: str, anthropic_api_key: str, unstract_api_key: str, gcloud_api_key: str,
            openai_model: str, claude_model: str,
            openai_files_cache_path: str,
            claude_wait_time:int = 30,
            max_claude_waits: int = 4
    ):
        self.openai_client = AsyncOpenAI(api_key=openai_api_key)
        set_default_openai_key(openai_api_key)
        self.anthropic_client = AsyncAnthropic(api_key=anthropic_api_key)
        self.claude_wait_time = claude_wait_time
        self.max_claude_waits = max_claude_waits

        self._sem = semaphore

        self.db_wages_file_path = db_wages_file_path
        self.payroll_file_path = payroll_file_path

        self.openai_compliance_matrix_prompt = openai_compliance_matrix_prompt
        self.openai_single_wage_check_prompt = openai_single_wage_check_prompt
        self.claude_compliance_matrix_prompt = claude_compliance_matrix_prompt
        self.claude_single_wage_check_prompt = claude_single_wage_check_prompt
        self.project_location_prompt = project_location_prompt

        self.gcloud_api_key = gcloud_api_key
        self.unstract_api_key = unstract_api_key

        self.openai_model = openai_model
        self.claude_model = claude_model

        self.openai_files_cache_path = openai_files_cache_path

        self.payroll_unstract_json = None
        self.payroll_ocr_str = None
        self.project_location_str = None
        self.openai_compliance_table = None


    async def ocr_payroll(self):
        async with self._sem:
            self.payroll_unstract_json = await async_whisper_pdf_text_extraction(
                unstract_api_key = self.unstract_api_key,
                input_pdf_path = self.payroll_file_path,
                return_json = True,
                add_line_nos = True,
            )
        self.payroll_ocr_str = self.payroll_unstract_json['result_text']
        return self.payroll_unstract_json

    async def get_project_location(self):
        search_location = create_search_location_tool(self.gcloud_api_key)
        location_agent = Agent(
            name="Project Location Extraction Agent",
            instructions=self.project_location_prompt,
            tools=[search_location],
            model=self.openai_model
        )
        upload_coroutines = [
            get_or_upload_async(
                file_path=path,
                client=self.openai_client,
                cache_path=self.openai_files_cache_path,
                purpose='user_data'
            )
            for path in [self.payroll_file_path, self.db_wages_file_path]
        ]
        async with self._sem:
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

        if self.payroll_ocr_str is not None:
            location_input[0]['content'].append({
                'type': 'input_text',
                'text': 'The following text was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + self.payroll_ocr_str
            })
        async with self._sem:
            with trace('Project Location Extraction Workflow'):
                location_result = await Runner.run(
                    location_agent,
                    input=location_input
                )
            self.project_location_str = location_result.final_output
        return self.project_location_str

    def get_db_wages_file_text(self, include_line_nos: bool = True, return_page_lengths: bool = False):
        page_lengths = []
        db_wages_doc = fitz.open(self.db_wages_file_path)
        db_wages_file_text = ''
        line_no = 0
        page_start_line_no = 0
        for page in db_wages_doc:
            page_text = page.get_text(sort=True).strip()
            for line in page_text.splitlines():
                if include_line_nos:
                    db_wages_file_text += f'{hex(line_no)}:{line}'+'\n'
                else:
                    db_wages_file_text += line + '\n'
                line_no += 1
            page_lengths.append(line_no - page_start_line_no)
            page_start_line_no = line_no
        db_wages_doc.close()
        if return_page_lengths:
            return db_wages_file_text, page_lengths
        return db_wages_file_text

    def get_payroll_citation_images_from_line_hexes(self, citation_line_hexes: list[str]):
        """Get citation images from line hex identifiers using the payroll OCR data."""
        citation_lines = [int(hex, 16)-1 for hex in citation_line_hexes] # the -1 is because unstract hex lines are 1-indexed
        line_whisper_boxes = [self.payroll_unstract_json['line_metadata'][line_ind] for line_ind in citation_lines]

        line_boxes = [
            {
                'page': line_box[0],
                'bbox': [0.01, (line_box[1] - line_box[2]) / line_box[3], 0.99, (line_box[1]) / line_box[3]]
            }
            for line_box in line_whisper_boxes
        ]

        citation_images, citation_pages = render_pdf_bboxes_to_images(
            citation_bboxes = line_boxes,
            pdf_source=self.payroll_file_path,
        )
        return citation_images, citation_pages

    def get_db_wages_citation_images_from_line_hexes(self, citation_line_hexes: list[str]):
        """Get citation images from the Davis-Bacon wages file based on a citation query."""
        # Get the text using the helper (without line numbers)
        citation_lines = [int(hex, 16) for hex in citation_line_hexes]  # convert hex to int
        db_wages_file_text, page_lengths = self.get_db_wages_file_text(include_line_nos=False, return_page_lengths= True)

        citation_pages_dict = get_lines_page_numbers(citation_lines, page_lengths)

        citation_pages = []
        citation_images = []
        db_wages_doc = fitz.open(self.db_wages_file_path)
        for page, lines in citation_pages_dict.items():
            citation_pages.append(page)
            citation_images.append(
                render_line_highlights(
                    text = db_wages_doc[page].get_text(sort=True).strip(),
                    highlight_lines = lines
                )
            )
        db_wages_doc.close()

        return citation_images, citation_pages

    async def openai_payroll_compliance_table(self):
        # openai extraction
        async with self._sem:
            payroll_file_id = await get_or_upload_async(
                file_path=self.payroll_file_path,
                client=self.openai_client,
                cache_path=self.openai_files_cache_path,
                purpose='user_data'
            )
        db_wages_file_text = self.get_db_wages_file_text(include_line_nos=True)
        openai_compliance_agent = Agent(
            name="Payroll Compliance Agent",
            instructions=self.openai_compliance_matrix_prompt,
            tools=[report_compliance_table, report_parsing_error],
            model=self.openai_model,
            tool_use_behavior='stop_on_first_tool'
        )
        openai_compliance_input = [
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'input_text',
                        'text': 'Here is the Davis-Bacon wage determination file, with hex line numbers:\n' + db_wages_file_text
                    },
                    {
                        'type': 'input_file',
                        'file_id': payroll_file_id
                    }
                ]
            }
        ]
        if self.payroll_ocr_str is not None:
            openai_compliance_input[0]['content'].append({
                'type': 'input_text',
                'text': 'The following was extracted from the payroll file via OCR. Use it for citations and to cross-reference with the payroll file:\n' + self.payroll_ocr_str
            })
        if self.project_location_str is not None:
            openai_compliance_input[0]['content'].append({
                'type': 'input_text',
                'text': 'The location of the project has been determined: \n' + self.project_location_str
            })
        async with self._sem:
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
        self.openai_compliance_table = openai_compliance_table
        return openai_compliance_table

    async def claude_payroll_compliance_table(self):
        """Generate compliance table using Claude."""
        with open(self.payroll_file_path, "rb") as f:
            payroll_bytes = f.read()
        payroll_base64_string = base64.b64encode(payroll_bytes).decode('utf-8')

        db_wages_file_text = self.get_db_wages_file_text(include_line_nos=True)

        claude_compliance_input = [
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'text',
                        'text': self.claude_compliance_matrix_prompt
                    },
                    {
                        'type': 'text',
                        'text': 'Here is the Davis-Bacon wage determination file, with hex line numbers:\n' + db_wages_file_text
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
        if self.payroll_ocr_str is not None:
            claude_compliance_input[0]['content'].append({
                'type': 'text',
                'text': 'The following was extracted from the payroll file via OCR. Use it for citations and to cross-reference with the payroll file:\n' + self.payroll_ocr_str
            })
        if self.project_location_str is not None:
            claude_compliance_input[0]['content'].append({
                'type': 'text',
                'text': 'The project is located in the following county/location: ' + self.project_location_str
            })
        async with self._sem:
            for wait in range(self.max_claude_waits):
                try:
                    claude_compliance_response = await self.anthropic_client.messages.create(
                        model=self.claude_model,
                        messages=claude_compliance_input,
                        max_tokens=10_000
                    )
                    break
                except anthropic.RateLimitError as e:
                    if wait+1 == self.max_claude_waits:
                        raise e
                    else:
                        await asyncio.sleep(self.claude_wait_time)



        claude_compliance_result = json.loads('{"success":' + claude_compliance_response.content[0].text)
        try:
            assert claude_compliance_result.get('success'), 'Claude indicated failure in compliance table extraction.'
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
                    compliance=wage_check['compliance'],
                    payroll_citation_lines=wage_check['payroll_citation_lines'],
                    wage_determination_citation_lines=wage_check['wage_determination_citation_lines']
                )
                for wage_check in claude_compliance_result['wage_checks']
            ]
            claude_compliance_table = ComplianceTable(
                payroll_name=claude_compliance_result.get('payroll_name', ''),
                wage_checks=claude_wage_checks
            )
        except Exception as e:
            print(f'Claude failed to extract compliance table with error {e}:\n{json.dumps(claude_compliance_result,indent=2)})')
            claude_compliance_table = None
        return claude_compliance_table

    async def openai_single_wage_check(self, employee_wage_check: EmployeeWageCheck):
        """Re-check a single employee's wage using OpenAI."""
        upload_coroutines = [
            get_or_upload_async(
                file_path=path,
                client=self.openai_client,
                cache_path=self.openai_files_cache_path,
                purpose='user_data'
            )
            for path in [self.db_wages_file_path, self.payroll_file_path]
        ]
        db_wages_file_id, payroll_file_id = await asyncio.gather(*upload_coroutines)
        openai_check_agent = Agent(
            name="Payroll Check Agent",
            instructions=self.openai_single_wage_check_prompt,
            tools=[report_wage_check, report_parsing_error],
            model=self.openai_model,
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
        if self.payroll_ocr_str is not None:
            openai_check_input[0]['content'].append({
                'type': 'input_text',
                'text': 'The following text was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + self.payroll_ocr_str
            })
        if self.project_location_str is not None:
            openai_check_input[0]['content'].append({
                'type': 'input_text',
                'text': 'The project is located in the following county/location: ' + self.project_location_str
            })
        openai_check_input[0]['content'].append({
            'type': 'input_text',
            'text': f'Please extract the payroll information for the following employee: {employee_wage_check.employee_name}'
        })
        async with self._sem:
            with trace(f'Payroll Checking Workflow for {employee_wage_check.employee_name}'):
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

    async def claude_single_wage_check(self, employee_wage_check: EmployeeWageCheck):
        """Re-check a single employee's wage using Claude."""
        with open(self.payroll_file_path, "rb") as f:
            payroll_bytes = f.read()
        payroll_base64_string = base64.b64encode(payroll_bytes).decode('utf-8')
        with open(self.db_wages_file_path, "rb") as f:
            db_wages_bytes = f.read()
        db_wages_base64_string = base64.b64encode(db_wages_bytes).decode('utf-8')
        claude_check_input = [
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'text',
                        'text': self.claude_single_wage_check_prompt
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
        if self.payroll_ocr_str is not None:
            claude_check_input[0]['content'].append({
                'type': 'text',
                'text': 'The following text was extracted from the payroll file via OCR. Use it to cross-reference with the payroll file:\n' + self.payroll_ocr_str
            })
        if self.project_location_str is not None:
            claude_check_input[0]['content'].append({
                'type': 'text',
                'text': 'The project is located in the following county/location: ' + self.project_location_str
            })
        claude_check_input[0]['content'].append({
            'type': 'text',
            'text': f'Please extract the payroll information for the following employee: {employee_wage_check.employee_name}'
        })

        async with self._sem:
            for wait in range(self.max_claude_waits):
                try:
                    claude_check_response = await self.anthropic_client.messages.create(
                        model=self.claude_model,
                        messages=claude_check_input,
                        max_tokens=10_000
                    )
                    break
                except anthropic.RateLimitError as e:
                    if wait+1 == self.max_claude_waits:
                        raise e
                    else:
                        await asyncio.sleep(self.claude_wait_time)

        new_wage_check = json.loads('{"success":' + claude_check_response.content[0].text)
        try:
            assert new_wage_check.get('success'), 'Claude indicated failure in single wage check extraction.'
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
                compliance=new_wage_check['compliance'],
                payroll_citation_lines=new_wage_check['payroll_citation_lines'],
                wage_determination_citation_lines=new_wage_check['wage_determination_citation_lines'],
            )
        except Exception as e:
            print(f'Claude failed to extract single wage check with error {e}:\n{json.dumps(new_wage_check,indent=2)})')
            claude_wage_check = None
        return claude_wage_check

    async def resolve_disputed_check(
            self,
            openai_wc: EmployeeWageCheck,
            claude_wc: EmployeeWageCheck
    ) -> Optional[EmployeeWageCheck]:
        """Resolve a disputed wage check by re-running both AI models."""
        openai_check, claude_check = await asyncio.gather(
            self.openai_single_wage_check(employee_wage_check=openai_wc),
            self.claude_single_wage_check(employee_wage_check=claude_wc)
        )
        if openai_check is None and claude_check is None:
            return None
        elif openai_check is None:
            return claude_check
        elif claude_check is None:
            return openai_check
        elif abs(openai_check.davis_bacon_total_rate - claude_check.davis_bacon_total_rate) > 0.1:
            return None
        elif abs(openai_check.paid_rate - claude_check.paid_rate) > 0.1:
            return None
        else:  # why do we throw away everything else? because its harder to match those strings. there may be idiosyncrasies in naming conventions
            return claude_check  # prefer claude

    async def get_payroll_compliance_table(self, name_match_threshold: float = 80.):
        """Get the payroll compliance table by running OCR, location extraction, and compliance checks."""
        # Run preliminary steps if not already done
        if self.payroll_unstract_json is None:
            print('Running OCR on payroll...')
            await self.ocr_payroll()
            print('OCR complete.')

        if self.project_location_str is None:
            print('Getting project location...')
            await self.get_project_location()
            print(f'Project location: {self.project_location_str}')

        # Run compliance tables from both AI models
        print('Getting compliance tables from OpenAI and Claude...')
        openai_compliance_table, claude_compliance_table = await asyncio.gather(
            self.openai_payroll_compliance_table(),
            self.claude_payroll_compliance_table()
        )
        print('Done.')

        if openai_compliance_table is None and claude_compliance_table is None:
            return None, None, None, None
        elif openai_compliance_table is None:
            return claude_compliance_table, None, None, None
        elif claude_compliance_table is None:
            return openai_compliance_table, None, None, None

        # if we reach here, both are non-null - concordance time

        # pair up wage checks by employee name similarity
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
        # resolved matched but disputed wage checks
        disputed_resolution_tasks = [
            self.resolve_disputed_check(openai_wc=openai_wc, claude_wc=claude_wc)
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

if __name__ == '__main__':
    # File paths
    db_wages_file_path = r"C:\Users\jaeckle\PycharmProjects\DavisBaconApp\documents\rates.pdf"
    payroll_file_path = r"C:\Users\jaeckle\PycharmProjects\DavisBaconApp\documents\coulson.pdf"

    # Load prompts
    with open('prompts/openai_compliance_matrix_prompt.md', 'r', encoding='utf-8') as f:
        openai_compliance_matrix_prompt = f.read()

    with open('prompts/openai_single_wage_check_prompt.md', 'r', encoding='utf-8') as f:
        openai_single_wage_check_prompt = f.read()

    with open('prompts/claude_compliance_matrix_prompt.md', 'r', encoding='utf-8') as f:
        claude_compliance_matrix_prompt = f.read()

    with open('prompts/claude_single_wage_check_prompt.md', 'r', encoding='utf-8') as f:
        claude_single_wage_check_prompt = f.read()

    with open('prompts/project_location_prompt.md', 'r', encoding='utf-8') as f:
        project_location_prompt = f.read()

    # Load configs
    import tomli

    with open('../GlobalUtils/config.toml', 'rb') as f:
        global_config = tomli.load(f)

    with open('config.toml', 'rb') as f:
        config = tomli.load(f)

    # Extract API keys and settings
    openai_api_key = global_config['openai_api_key']
    anthropic_api_key = global_config['anthropic_api_key']
    unstract_api_key = global_config['unstract_api_key']
    gcloud_api_key = global_config['gcloud_api_key']
    openai_model = 'gpt-5.1'
    claude_model = config['claude_model']
    openai_files_cache_path = global_config['openai_files_cache_path']


    compliance_checker = ComplianceChecker(
        db_wages_file_path=db_wages_file_path,
        payroll_file_path=payroll_file_path,
        openai_compliance_matrix_prompt=openai_compliance_matrix_prompt,
        openai_single_wage_check_prompt=openai_single_wage_check_prompt,
        claude_compliance_matrix_prompt=claude_compliance_matrix_prompt,
        claude_single_wage_check_prompt=claude_single_wage_check_prompt,
        project_location_prompt=project_location_prompt,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
        unstract_api_key=unstract_api_key,
        gcloud_api_key=gcloud_api_key,
        openai_model=openai_model,
        claude_model=claude_model,
        openai_files_cache_path=openai_files_cache_path
    )
    pass