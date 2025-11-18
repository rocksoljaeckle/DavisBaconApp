from agents import *
import streamlit as st
from streamlit_image_zoom import image_zoom
from anthropic import AsyncAnthropic
from openai import OpenAI, AsyncOpenAI
import asyncio
import tomli
from pandas import DataFrame
import uuid
import os
import ftfy
import tempfile
from functools import partial
from rapidfuzz import fuzz
from rapidfuzz.utils import default_process as rapidfuzz_default_process
import time
import fitz

import nest_asyncio
nest_asyncio.apply() # todo this is hacky - necessary?


from db_utils import (
    get_payroll_compliance_table,
    get_db_wages_citation_images,
    get_project_location,
    ComplianceTable,
    EmployeeWageCheck
)
from GlobalUtils.ocr import google_ocr_pdf_text_overlay, async_whisper_pdf_text_extraction
from GlobalUtils.citation import (
    get_unstract_citation_images,
    render_line_highlights,
    find_best_openai_lines
)
from GlobalUtils.st_file_serving import StreamlitPDFServer

st.set_page_config(layout = 'wide', page_title = 'Davis-Bacon Payroll Checker', page_icon = '✅')

st.image('https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Bacon_Davis.jpg/1920px-Bacon_Davis.jpg', width=200)


@st.cache_data
def load_config():
    with open('config.toml', 'rb') as f:
        return tomli.load(f)

def fix_table_checks(compliance_table: ComplianceTable):
    """Fix mojibake in the compliance checks in ComplianceTable objects."""
    for wage_check in compliance_table.wage_checks:
        wage_check.compliance = ftfy.fix_text(wage_check.compliance)
    return compliance_table

def destroy_file_servers():
    if 'payroll_file_server' in st.session_state:
        st.session_state['payroll_file_server'].destroy()
        del st.session_state['payroll_file_server']
    if 'db_wages_file_server' in st.session_state:
        st.session_state['db_wages_file_server'].destroy()
        del st.session_state['db_wages_file_server']



@st.dialog('View Citation Source', width='large', on_dismiss = destroy_file_servers)
def show_citation_dialog(
        wage_check: EmployeeWageCheck,
        payroll_file_path: str,
        db_wages_file_path: str,
        payroll_unstract_json: dict
):
    """Show citation source for a given wage check."""
    st.markdown(f'### Finding source for: {wage_check.employee_name}')
    st.markdown(f'**Title:** {wage_check.title} | **Paid Rate:** \\${wage_check.paid_rate:,.2f} | **Davis-Bacon Classification:** {wage_check.davis_bacon_classification} | **Davis-Bacon Total Rate:** \\${wage_check.davis_bacon_total_rate:,.2f}')

    # Generate citation query and cache key
    payroll_citation_query = f'Employee Name: {wage_check.employee_name}, Paid Rate: {wage_check.paid_rate}'
    db_wages_citation_query = f'Please find lines for the classification _and_ the rate - Davis-Bacon Classification: {wage_check.davis_bacon_classification}, Davis-Bacon Base Rate: {wage_check.davis_bacon_base_rate}, Davis-Bacon Fringe Rate: {wage_check.davis_bacon_fringe_rate}'
    cache_key = f'{payroll_file_path}_{payroll_citation_query}_{db_wages_citation_query}'

    # Initialize cache if it doesn't exist
    if 'citation_cache' not in st.session_state:
        st.session_state['citation_cache'] = {}

    start_time = time.time()
    # Check if citation is already cached
    if cache_key in st.session_state['citation_cache']:
        (db_wages_citation_images, db_wages_citation_page_numbers), (payroll_citation_images, payroll_citation_page_numbers) = st.session_state['citation_cache'][cache_key]
        st.info('Loaded source from cache')
    else:
        # Initialize OpenAI client
        openai_client = AsyncOpenAI(api_key=st.session_state['global_config']['openai_api_key'])

        with st.spinner('Generating citation (~30 seconds) ...', show_time=True):
            try:
                payroll_citation_task = get_unstract_citation_images(
                    pdf_source=payroll_file_path,
                    unstract_response_json=payroll_unstract_json,
                    citation_query=payroll_citation_query,
                    citation_prompt=st.session_state['citation_prompt'],
                    openai_client=openai_client,
                    return_page_numbers=True
                )

                db_wages_citation_task = get_db_wages_citation_images(
                    db_wages_file_path = db_wages_file_path,
                    db_wages_citation_query = db_wages_citation_query,
                    citation_prompt=st.session_state['citation_prompt'],
                    openai_client = openai_client,
                )
                (db_wages_citation_images, db_wages_citation_page_numbers),  (payroll_citation_images, payroll_citation_page_numbers) = asyncio.run(
                    asyncio.gather(
                        db_wages_citation_task,
                        payroll_citation_task
                    )
                )
                # Cache the result
                st.session_state['citation_cache'][cache_key] = (db_wages_citation_images, db_wages_citation_page_numbers), (payroll_citation_images, payroll_citation_page_numbers)
            except Exception as e:
                st.error(f'Error generating citation: {str(e)}')
                raise e
                # return
    end_time = time.time()

    if payroll_citation_images:
        n_citations = len(payroll_citation_images)+len(db_wages_citation_images)
        st.success(f'Found {n_citations} citation(s) in {end_time - start_time:.2f} seconds.')

        main_col, margin = st.columns([8,1])
        with main_col:
            if 'payroll_file_server' not in st.session_state:
                st.session_state['payroll_file_server'] = StreamlitPDFServer(payroll_file_path)
                st.session_state['payroll_file_server'].serve_pdf()
            if 'db_wages_file_server' not in st.session_state:
                st.session_state['db_wages_file_server'] = StreamlitPDFServer(db_wages_file_path)
                st.session_state['db_wages_file_server'].serve_pdf()

            with st.expander('Payroll source(s)', expanded = True):
                for i, (img, page_no) in enumerate(zip(payroll_citation_images, payroll_citation_page_numbers)):
                    with st.container(key=f'aura_payroll_{i}'):
                        image_zoom(img, mode = 'both', size = 1024, keep_resolution = True, zoom_factor = 4., increment = .3)
                    page_link = st.session_state['payroll_file_server'].get_page_link(page=page_no)
                    st.markdown(f'[View page in document (Page {page_no + 1}) :material/open_in_new:]({page_link})')
                    if i < len(payroll_citation_images)-1:
                        st.divider()
            with st.expander('Davis-Bacon wage determination source(s)', expanded = True):
                for i, (img, page_no) in enumerate(zip(db_wages_citation_images, db_wages_citation_page_numbers)):
                    with st.container(key=f'aura_db_{i}'):
                        image_zoom(img, mode = 'both', size = 1024, keep_resolution = True, zoom_factor = 4., increment = .3)
                    page_link = st.session_state['db_wages_file_server'].get_page_link(page=page_no)
                    st.markdown(f'[View page in document (Page {page_no+1}) :material/open_in_new:]({page_link})')
                    if i < len(db_wages_citation_images)-1:
                        st.divider()
        with margin:
            with st.container(height='stretch', width='stretch', key='gray-background'):
                st.write(' ')
                pass
    else:
        st.warning('No citations found for this employee. The source may not be clearly identifiable in the document.')

def render_compliance_results():
    """Render compliance results stored in session state."""
    if st.session_state['failed_indices']:
        st.error(
            f'Compliance check failed for {len(st.session_state['failed_indices'])} payroll files: \n{", ".join([st.session_state['compliance_results'][i]['file_name'] for i in st.session_state['failed_indices']])}')
    st.info('Compliance tables successfully loaded')
    st.markdown('### Compliance Results:')

    tables_html = ''
    for compliance_result in st.session_state['compliance_results']:
        compliance_table = compliance_result['compliance_table']
        data_rows = [wage_check.model_dump() for wage_check in compliance_table.wage_checks]
        data_rows = [{key: value for key, value in row.items() if key not in ['compliance_reasoning', 'overtime_rate']} for row in data_rows]
        table_df = DataFrame(data_rows)
        tables_html += f'\n<p style = "font-size: 25px;"><br><br>{compliance_table.payroll_name}<br></p>' + table_df.to_html()
    st.download_button(
        label = 'Download payroll compliance results as HTML',
        data = tables_html,
        file_name = 'compliance.html',
        mime = 'text/html'
    )

    if st.button('Clear Results'):
        del st.session_state['compliance_results']
        del st.session_state['payroll_files_paths']
        del st.session_state['db_wages_file_path']
        del st.session_state['failed_indices']
        del st.session_state['payroll_unstract_jsons']
        del st.session_state['citation_cache']
        st.rerun()

    show_reasoning = st.checkbox('Show compliance reasoning', value=False)

    if 'editor_keys' not in st.session_state:
        st.session_state['editor_keys'] = [0 for _ in st.session_state['compliance_results']]
    citation_available = 'payroll_unstract_jsons' in st.session_state
    compliance_column_config = {
        'employee_name': st.column_config.Column('Employee Name', width=100),
        'title': st.column_config.Column('Payroll Title', width=150),
        'davis_bacon_classification': st.column_config.Column('Davis-Bacon Classification', width=400),
        'davis_bacon_base_rate': st.column_config.NumberColumn('Base Rate (DB)', format='dollar', width=30),
        'davis_bacon_fringe_rate': st.column_config.NumberColumn('Fringe Rate (DB)', format='dollar', width=30),
        'davis_bacon_total_rate': st.column_config.NumberColumn('Total Rate (DB)', format='dollar', width=30),
        'paid_rate': st.column_config.NumberColumn('Payroll Rate', format='dollar', width=30),
        'compliance': st.column_config.Column('Compliant?', width=150),
        'citation': st.column_config.CheckboxColumn('Show Citation', disabled = not citation_available, width=50)
    }
    if not show_reasoning:
        compliance_column_config['compliance_reasoning'] = None
    for payroll_index, compliance_result in enumerate(st.session_state['compliance_results']):
        file_name = compliance_result['file_name']
        compliance_table = compliance_result['compliance_table']
        with st.expander(label=f'({file_name}) - {compliance_table.payroll_name}', expanded=True):
            st.write(f'**Project location**: {st.session_state['project_location_strs'][payroll_index]}')
            data = DataFrame(
                [wage_check.model_dump() for wage_check in compliance_table.wage_checks]
            )
            data['citation'] = False
            data.drop('overtime_rate', axis=1, inplace=True, errors = 'ignore')
            new_data = st.data_editor(data, column_config=compliance_column_config, key = st.session_state['editor_keys'][payroll_index])
            if compliance_result['disputed_wage_checks']:
                st.warning('Disputed Wage Check between OpenAI and Claude for employees: '+', '.join([openai_wc.employee_name for openai_wc, claude_wc in compliance_result['disputed_wage_checks']]))
                for openai_wc, claude_wc in compliance_result['disputed_wage_checks']:
                    st.markdown(f'**{openai_wc.employee_name}**')
                    if fuzz.ratio(openai_wc.title, claude_wc.title, processor = rapidfuzz_default_process) < 80.:
                        st.markdown(f'  - **Title Mismatch:** OpenAI: "{openai_wc.title}" | Claude: "{claude_wc.title}"')
                    else:
                        st.markdown(f'  - Title: "{openai_wc.title}"')
                    if fuzz.ratio(openai_wc.davis_bacon_classification, claude_wc.davis_bacon_classification, processor = rapidfuzz_default_process) < 80.:
                        st.markdown(f'  - **Davis-Bacon Classification Mismatch:** OpenAI: "{openai_wc.davis_bacon_classification}" | Claude: "{claude_wc.davis_bacon_classification}"')
                    else:
                        st.markdown(f'  - Davis-Bacon Classification: "{openai_wc.davis_bacon_classification}"')
                    if abs(openai_wc.davis_bacon_total_rate - claude_wc.davis_bacon_total_rate)>0.1:
                        st.markdown(f'  - **Davis-Bacon Total Rate Mismatch:** OpenAI: {openai_wc.davis_bacon_total_rate} | Claude: {claude_wc.davis_bacon_total_rate}')
                    else:
                        st.markdown(f'  - Davis-Bacon Total Rate: {openai_wc.davis_bacon_total_rate}')
                    if abs(openai_wc.paid_rate - claude_wc.paid_rate)>0.1:
                        st.markdown(f'  - **Paid Rate Mismatch:** OpenAI: {openai_wc.paid_rate} | Claude: {claude_wc.paid_rate}')
                    else:
                        st.markdown(f'  - Paid Rate: {openai_wc.paid_rate}')
                    with st.popover('OpenAI Compliance Reasoning'):
                        st.markdown(f'**OpenAI Reasoning:** {openai_wc.compliance_reasoning}')
                    with st.popover('Claude Compliance Reasoning'):
                        st.markdown(f'**Claude Reasoning:** {claude_wc.compliance_reasoning}')
            if compliance_result['unmatched_openai']:
                st.warning('The following wage checks were found only by OpenAI:')
                for openai_wc in compliance_result['unmatched_openai']:
                    st.markdown(f'  - {openai_wc.employee_name}, Title: "{openai_wc.title}", DB Classification: "{openai_wc.davis_bacon_classification}", DB Total Rate: {openai_wc.davis_bacon_total_rate}, Paid Rate: {openai_wc.paid_rate}')
            if compliance_result['unmatched_claude']:
                st.warning('The following wage checks were found only by Claude:')
                for claude_wc in compliance_result['unmatched_claude']:
                    st.markdown(f'  - {claude_wc.employee_name}, Title: "{claude_wc.title}", DB Classification: "{claude_wc.davis_bacon_classification}", DB Total Rate: {claude_wc.davis_bacon_total_rate}, Paid Rate: {claude_wc.paid_rate}')

            for wage_check_ind, row in new_data.iterrows():
                if row['citation']:
                    # print('uh oh')
                    st.session_state['editor_keys'][payroll_index] += 1 # force refresh of data editor
                    show_citation_dialog(
                        wage_check = compliance_table.wage_checks[wage_check_ind],
                        payroll_file_path = st.session_state['payroll_files_paths'][payroll_index],
                        db_wages_file_path=st.session_state['db_wages_file_path'],
                        payroll_unstract_json=st.session_state['payroll_unstract_jsons'][payroll_index],
                    )


def get_compliance_results(
        payroll_files,
        db_wages_file,
        file_processing_mode: str
):
    """Get compliance results for uploaded payroll and Davis-Bacon wages files."""

    st.success('Files uploaded successfully!')
    config_dict = load_config()
    files_save_dir = config_dict['files_save_dir']
    openai_api_key = config_dict['openai_api_key']
    openai_client = AsyncOpenAI(api_key=openai_api_key)
    anthropic_api_key = st.session_state['global_config']['anthropic_api_key']
    anthropic_client = AsyncAnthropic(api_key=anthropic_api_key)
    set_default_openai_key(openai_api_key)
    openai_files_cache_path = config_dict['openai_files_cache_path']

    upload_files = [*payroll_files, db_wages_file]
    file_paths = []
    for file in upload_files:
        curr_path = os.path.join(files_save_dir, f'{file.name}_{uuid.uuid4().hex[:7]}.pdf')
        with open(curr_path, 'wb') as f:
            f.write(file.read())
        file_paths.append(curr_path)
    db_wages_file_path = file_paths[-1]
    st.session_state['payroll_files_paths'] = file_paths[:-1]
    st.session_state['db_wages_file_path'] = db_wages_file_path
    # db_wages_doc = fitz.open(db_wages_file_path)
    # db_wages_file_text = ''
    # for page in db_wages_doc:
    #     db_wages_file_text += page.get_text() + '\n'
    # db_wages_doc.close()
    # st.session_state['db_wages_file_text'] = db_wages_file_text

    with open(config_dict['openai_compliance_matrix_prompt_path'], 'r', encoding='utf-8') as f:
        openai_compliance_matrix_prompt = f.read()
    with open(config_dict['openai_single_wage_check_prompt_path'], 'r', encoding='utf-8') as f:
        openai_single_wage_check_prompt = f.read()
    with open(config_dict['claude_compliance_matrix_prompt_path'], 'r', encoding='utf-8') as f:
        claude_compliance_matrix_prompt = f.read()
    with open(config_dict['claude_single_wage_check_prompt_path'], 'r', encoding='utf-8') as f:
        claude_single_wage_check_prompt = f.read()
    with open(config_dict['project_location_prompt_path'], 'r', encoding='utf-8') as f:
        project_location_prompt = f.read()

    get_location_partial = partial(
        get_project_location,
        project_location_prompt = project_location_prompt,
        db_wages_file_path = db_wages_file_path,
        gcloud_api_key = st.session_state['global_config']['gcloud_api_key'],
        openai_files_cache_path = openai_files_cache_path,
        openai_client=openai_client,
        openai_model=config_dict['openai_model']
    )
    compliance_check_partial = partial(
        get_payroll_compliance_table,
        openai_client=openai_client,
        openai_model=config_dict['openai_model'],
        openai_compliance_matrix_prompt=openai_compliance_matrix_prompt,
        openai_single_wage_check_prompt = openai_single_wage_check_prompt,
        anthropic_client=anthropic_client,
        claude_model=config_dict['claude_model'],
        claude_compliance_matrix_prompt=claude_compliance_matrix_prompt,
        claude_single_wage_check_prompt = claude_single_wage_check_prompt,
        db_wages_file_path=db_wages_file_path,
        openai_files_cache_path=openai_files_cache_path
    )
    match file_processing_mode:
        case 'none':
            project_locations_tasks = [get_location_partial(payroll_file_path = file_path) for file_path in st.session_state['payroll_files_paths']]
            st.session_state['project_location_strs'] = asyncio.run(asyncio.gather(*project_locations_tasks))
            compliance_check_coroutines = [
                compliance_check_partial(
                    payroll_file_path = file_path,
                    project_location_str = project_location_str
                )
                for file_path, project_location_str in zip(st.session_state['payroll_files_paths'], st.session_state['project_location_strs'])
            ]
        case 'google ocr':
            ocred_payroll_paths = []
            with tempfile.TemporaryDirectory() as tmpdirname:
                for payroll_file_path in st.session_state['payroll_files_paths']:
                    output_pdf_path = os.path.join(tmpdirname, 'ocr_'+os.path.basename(payroll_file_path))
                    google_ocr_pdf_text_overlay(
                        input_pdf_path=payroll_file_path,
                        output_pdf_path=output_pdf_path,
                        dpi=300
                    )
                    ocred_payroll_paths.append(output_pdf_path)
                project_locations_tasks = [get_location_partial(payroll_file_path=file_path) for file_path in ocred_payroll_paths]
                st.session_state['project_location_strs'] = asyncio.run(asyncio.gather(*project_locations_tasks))
                compliance_check_coroutines = [
                    compliance_check_partial(
                        payroll_file_path=file_path,
                        project_location_str=project_location_str
                    )
                    for file_path, project_location_str in zip(ocred_payroll_paths, st.session_state['project_location_strs'])
                ]
        case 'unstract whisper':
            ocr_coroutines = [
                async_whisper_pdf_text_extraction(
                    unstract_api_key = st.session_state['global_config']['unstract_api_key'],
                    input_pdf_path = file_path,
                    return_json = True
                )
                for file_path in st.session_state['payroll_files_paths']
            ]
            st.session_state['payroll_unstract_jsons'] = asyncio.run(asyncio.gather(*ocr_coroutines))
            payroll_ocr_strs = [unstract_json['result_text'] for unstract_json in st.session_state['payroll_unstract_jsons']]
            project_locations_tasks = [
                get_location_partial(
                    payroll_file_path = file_path,
                    payroll_ocr_str = ocr_str
                )
                for file_path, ocr_str in zip(st.session_state['payroll_files_paths'], payroll_ocr_strs)
            ]
            st.session_state['project_location_strs'] = asyncio.run(asyncio.gather(*project_locations_tasks))
            compliance_check_coroutines = [
                compliance_check_partial(
                    payroll_file_path = file_path,
                    payroll_ocr_str = ocr_str,
                    project_location_str = project_location_str
                )
                for file_path, ocr_str, project_location_str in zip(st.session_state['payroll_files_paths'], payroll_ocr_strs, st.session_state['project_location_strs'])
            ]
    task_results = asyncio.run(asyncio.gather(*compliance_check_coroutines))
    compliance_results = []
    failed_indices = []
    for payroll_ind in range(len(st.session_state['payroll_files_paths'])):
        file_name = payroll_files[payroll_ind].name
        compliance_table, disputed_wage_checks, unmatched_openai, unmatched_claude = task_results[payroll_ind]
        if compliance_table is not None:
            compliance_table = fix_table_checks(compliance_table)
        compliance_results.append(
            {
                'file_name': file_name,
                'compliance_table': compliance_table,
                'disputed_wage_checks': disputed_wage_checks,
                'unmatched_openai': unmatched_openai,
                'unmatched_claude': unmatched_claude
            }
        )
        if compliance_table is None:
            failed_indices.append(payroll_ind)
    return compliance_results, failed_indices

if 'global_config' not in st.session_state:
    with open('../GlobalUtils/config.toml', 'rb') as f:
        st.session_state['global_config'] = tomli.load(f)

if 'citation_prompt' not in st.session_state:
    with open(st.session_state['global_config']['citation_prompt_path'], 'r', encoding='utf-8') as f:
        st.session_state['citation_prompt'] = f.read()

# <editor-fold> CSS for page styling
gray_background_css = '''
<style>
.st-key-gray-background {
    background-color: #eaeaea;
}
</style>
'''
st.html(gray_background_css)

# glowing aura for citations
aura_css = """
<style>
[class*="st-key-aura"] {
    transition: box-shadow 0.3s ease;
}

[class*="st-key-aura"]:hover {
    box-shadow: 0 0 10px rgba(0, 168, 232, 0.6),
                0 0 20px rgba(0, 168, 232, 0.4),
                0 0 30px rgba(0, 168, 232, 0.2) !important;
    animation: pulse-glow 2s ease-in-out infinite !important;
}

@keyframes pulse-glow {
    0%, 100% {
        box-shadow: 0 0 10px rgba(0, 168, 232, 0.6),
                    0 0 20px rgba(0, 168, 232, 0.4),
                    0 0 30px rgba(0, 168, 232, 0.2) !important;
    }
    50% {
        box-shadow: 0 0 15px rgba(0, 168, 232, 0.8),
                    0 0 30px rgba(0, 168, 232, 0.6),
                    0 0 45px rgba(0, 168, 232, 0.4) !important;
    }
}
</style>
"""
st.html(aura_css)

# </editor-fold>

st.title('Davis-Bacon Payroll Checker')
st.markdown('_AI generated results are not guaranteed to be accurate._')
st.markdown('**Uploaded files will be sent to OpenAI and Anthropic via API. Their policies (as of 11/18/25) are not to use this data to train their models. Check the [OpenAI:material/open_in_new:](https://platform.openai.com/docs/guides/your-data) and [Anthropic:material/open_in_new:](https://privacy.claude.com/en/collections/10663361-commercial-customers) privacy pages for the most recent privacy information.**')

if 'compliance_results' not in st.session_state:

    l_col, r_margin = st.columns([1, 2], gap='large')

    payroll_files = l_col.container(border= True).file_uploader('**Upload the payroll files**', type = 'pdf', accept_multiple_files=True)

    db_wages_file = l_col.container(border= True).file_uploader('**Upload the Davis-Bacon wages file**', type = 'pdf', accept_multiple_files=False)

    file_processing_mode = st.pills(
        label = 'file processing mode ("unstract whisper" recommended)', options=['none', 'google ocr', 'unstract whisper'], default = 'unstract whisper', selection_mode = 'single')

    if st.button('Check Payroll Compliance'):
        if payroll_files and db_wages_file:
            with st.spinner('Checking compliance (may take several minutes)...', show_time=True):
                st.session_state['compliance_results'], st.session_state['failed_indices'] = get_compliance_results(payroll_files, db_wages_file, file_processing_mode)
            st.rerun()
        else:
            st.error('Please upload both payroll files and the Davis-Bacon wages file.')
else:
    render_compliance_results()
    st.stop()