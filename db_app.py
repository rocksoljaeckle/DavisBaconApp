from agents import *
import streamlit as st
from st_aggrid import (
    AgGrid,
    GridOptionsBuilder,
    JsCode,
)
from streamlit_image_zoom import image_zoom
import asyncio
import tomli
from pandas import DataFrame
import uuid
import os
import ftfy
from rapidfuzz import fuzz
from rapidfuzz.utils import default_process as rapidfuzz_default_process
import time

import nest_asyncio
nest_asyncio.apply() # todo this is hacky - necessary?


from db_utils import ComplianceChecker, EmployeeWageCheck, ComplianceTable

class DisputeItem:
    """Class to hold information about a single disputed attribute of a single employee"""
    def __init__(self, openai_item, claude_item):
        self.matched = None
        self.openai_item = openai_item
        self.claude_item = claude_item

class DisputeTable:
    def __init__(self, disputed_wage_checks: list[tuple[EmployeeWageCheck, EmployeeWageCheck]]):
        self.wage_checks = disputed_wage_checks # List of tuples: (openai_wage_check, claude_wage_check)
        self.disputed_items_dicts = []  # List of dicts with dispute details
        for dispute_ind, (openai_wc, claude_wc) in enumerate(disputed_wage_checks):
            items_dict = {'index': dispute_ind, 'employee_name': openai_wc.employee_name}

            title_item = DisputeItem(openai_wc.title, claude_wc.title)
            if fuzz.ratio(openai_wc.title, claude_wc.title, processor=rapidfuzz_default_process) < 80.:
                title_item.matched = False
            else:
                title_item.matched = True
            items_dict['title'] = title_item

            db_class_item = DisputeItem(openai_wc.davis_bacon_classification, claude_wc.davis_bacon_classification)
            if fuzz.ratio(openai_wc.davis_bacon_classification, claude_wc.davis_bacon_classification,
                          processor=rapidfuzz_default_process) < 80.:
                db_class_item.matched = False
            else:
                db_class_item.matched = True
            items_dict['davis_bacon_classification'] = db_class_item

            db_total_rate_item = DisputeItem(openai_wc.davis_bacon_total_rate, claude_wc.davis_bacon_total_rate)
            if abs(openai_wc.davis_bacon_total_rate - claude_wc.davis_bacon_total_rate) > 0.1:
                db_total_rate_item.matched = False
            else:
                db_total_rate_item.matched = True
            items_dict['davis_bacon_total_rate'] = db_total_rate_item

            paid_rate_item = DisputeItem(openai_wc.paid_rate, claude_wc.paid_rate)
            if abs(openai_wc.paid_rate - claude_wc.paid_rate) > 0.1:
                paid_rate_item.matched = False
            else:
                paid_rate_item.matched = True
            items_dict['paid_rate'] = paid_rate_item

            compliance_item = DisputeItem(openai_wc.compliance, claude_wc.compliance)
            if openai_wc.compliance != claude_wc.compliance:
                compliance_item.matched = False
            else:
                compliance_item.matched = True
            items_dict['compliance'] = compliance_item
            self.disputed_items_dicts.append(items_dict)

    def get_df(self):
        data_dicts = []
        for dispute in self.disputed_items_dicts:
            data_dict = {'index': dispute['index'], 'employee_name': dispute['employee_name']}
            for key in ['title', 'davis_bacon_classification', 'davis_bacon_total_rate', 'paid_rate', 'compliance']:
                item: DisputeItem = dispute[key]
                if not item.matched:
                    data_dict[key] = 'DISPUTED'
                else:
                    data_dict[key] = item.openai_item
            data_dicts.append(data_dict)
        return DataFrame(data_dicts)

    def get_row_markdown(self, row_ind: int):
        dispute = self.disputed_items_dicts[row_ind]
        md_lines = []
        for key in ['title', 'davis_bacon_classification', 'davis_bacon_total_rate', 'paid_rate', 'compliance']:
            item: DisputeItem = dispute[key]
            if item.matched:
                md_lines.append(f'- :green[**{key.replace("_", " ").title()}**: "{item.openai_item}" (AGREED)]')
            else:
                md_lines.append(f'- :red[**{key.replace("_", " ").title()}**: (DISPUTED)]\n\n  - OpenAI: "{item.openai_item}"\n\n  - Claude: "{item.claude_item}"\n')

        return '\n'.join(md_lines)

@st.cache_data
def load_config():
    with open('config.toml', 'rb') as f:
        return tomli.load(f)

def fix_table_checks(compliance_table: ComplianceTable):
    """Fix mojibake in the compliance checks in ComplianceTable objects."""
    for wage_check in compliance_table.wage_checks:
        wage_check.compliance = ftfy.fix_text(wage_check.compliance)
    return compliance_table

def reset_st_session_state():
    keys_to_clear = [
        'compliance_results',
        'payroll_files_paths',
        'db_wages_file_path',
        'failed_indices',
        'citation_cache',
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]

@st.dialog("Help & Documentation", width="large")
def show_help():
    with open("documents/davis-bacon-help.html", "r", encoding = 'utf-8') as f:
        st.html(f.read())

def get_compliance_symbol(compliance: str):
    """Get compliance symbol for a given compliance status."""
    compliance = ftfy.fix_text(compliance)
    match compliance:
        case '✓':
            return '✅'
        case '✗':
            return '❌'
        case '?':
            return '❓'
        case _:
            return compliance

@st.dialog('View Citation Source', width='large')
def show_citation_dialog(
        wage_check: EmployeeWageCheck,
        compliance_checker: ComplianceChecker,
        payroll_index: int,
        employee_index: int,
        disputed: bool = False,
        payroll_citation_line_hexes_override: list[str] | None = None,
        db_wages_citation_line_hexes_override: list[str] | None = None,
):
    """Show citation source for a given wage check."""
    st.markdown(f'### Finding source for: {wage_check.employee_name}')
    if not disputed:
        st.markdown(f'**Title:** {wage_check.title} | **Paid Rate:** \\${wage_check.paid_rate:,.2f} | **Davis-Bacon Classification:** {wage_check.davis_bacon_classification} | **Davis-Bacon Total Rate:** \\${wage_check.davis_bacon_total_rate:,.2f} | Compliance: {get_compliance_symbol(wage_check.compliance)}')

    # Initialize cache if it doesn't exist
    if 'citation_cache' not in st.session_state:
        st.session_state['citation_cache'] = {}
    if disputed:
        cache_key = f'disputed_{payroll_index}_{employee_index}'
    else:
        cache_key = f'{payroll_index}_{employee_index}'

    start_time = time.time()
    # Check if citation is already cached
    if cache_key in st.session_state['citation_cache']:
        (db_wages_citation_images, db_wages_citation_page_numbers), (payroll_citation_images, payroll_citation_page_numbers) = st.session_state['citation_cache'][cache_key]
        st.info('Loaded source from cache')
    else:
        with st.spinner('Generating citation (<10 seconds) ...', show_time=True):
            try:
                if payroll_citation_line_hexes_override is None:
                    payroll_citation_line_hexes = wage_check.payroll_citation_lines
                else:
                    payroll_citation_line_hexes = payroll_citation_line_hexes_override
                if db_wages_citation_line_hexes_override is None:
                    wage_determination_citation_line_hexes = wage_check.wage_determination_citation_lines
                else:
                    wage_determination_citation_line_hexes = db_wages_citation_line_hexes_override
                payroll_citation_images, payroll_citation_page_numbers = compliance_checker.get_payroll_citation_images_from_line_hexes(
                    citation_line_hexes=payroll_citation_line_hexes
                )

                db_wages_citation_images, db_wages_citation_page_numbers = compliance_checker.get_db_wages_citation_images_from_line_hexes(
                    citation_line_hexes=wage_determination_citation_line_hexes
                )
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
            with st.expander('Payroll source(s)', expanded = True):
                for i, (img, page_no) in enumerate(zip(payroll_citation_images, payroll_citation_page_numbers)):
                    with st.container(key=f'aura_payroll_{i}'):
                        image_zoom(img, mode = 'both', size = 1024, keep_resolution = True, zoom_factor = 4., increment = .3)
                    st.caption(f'Page {page_no + 1}')
                    if i < len(payroll_citation_images)-1:
                        st.divider()
            with st.expander('Davis-Bacon wage determination source(s)', expanded = True):
                for i, (img, page_no) in enumerate(zip(db_wages_citation_images, db_wages_citation_page_numbers)):
                    with st.container(key=f'aura_db_{i}'):
                        image_zoom(img, mode = 'both', size = 1024, keep_resolution = True, zoom_factor = 4., increment = .3)
                    st.caption(f'Page {page_no + 1}')
                    if i < len(db_wages_citation_images)-1:
                        st.divider()
        with margin:
            with st.container(height='stretch', width='stretch', key='gray-background'):
                st.write(' ')
                pass
    else:
        st.warning('No citations found for this employee. The source may not be clearly identifiable in the document.')

def show_employee_additional_info(wage_check: EmployeeWageCheck, compliance_checker: ComplianceChecker, employee_index: int, payroll_index: int):
    """Show additional information for a given employee."""
    compliance_check_symbol = get_compliance_symbol(wage_check.compliance)
    st.markdown(f'**"{wage_check.employee_name}**": {compliance_check_symbol}')
    l_col, r_col = st.columns([1, 9])
    with l_col.popover('Additional Info'):
        st.markdown(f'**Employee Name:** {wage_check.employee_name}')
        st.markdown(f'**Title:** {wage_check.title}')
        st.markdown(f'**Davis-Bacon Classification:** {wage_check.davis_bacon_classification}')
        st.markdown(f'**Davis-Bacon Total Rate:** ${wage_check.davis_bacon_total_rate:,.2f}')
        st.markdown(f'**Paid Rate:** ${wage_check.paid_rate:,.2f}')
        st.markdown(f'**Compliance:** {compliance_check_symbol}')
        st.markdown('**Reasoning:**')
        st.html(f"""
                <div style="font-size:16px; font-family: monospace;">
                {wage_check.compliance_reasoning}
                </div>
                """)
    if r_col.button(f'show citation for {wage_check.employee_name}',
                    key=f'show_citation_{payroll_index}_{employee_index}'):
        show_citation_dialog(
            wage_check=wage_check,
            compliance_checker=compliance_checker,
            payroll_index=payroll_index,
            employee_index=employee_index,
            disputed = False
        )

def show_disputed_employee_additional_info(
        selected_openai_wage_check: EmployeeWageCheck, 
        selected_claude_wage_check: EmployeeWageCheck, 
        selected_dispute_index: int,
        payroll_index: int,
        dispute_table: DisputeTable,
        compliance_checker: ComplianceChecker,
):
    st.write(f'#### Selected - {selected_openai_wage_check.employee_name}')
    st.write(dispute_table.get_row_markdown(selected_dispute_index))
    l_col, mid_col, r_col = st.columns([1, 1, 8])
    with l_col.popover('OpenAI reasoning'):
        st.html(f"""
        <div style="font-size:16px; font-family: monospace;">
            {selected_openai_wage_check.compliance_reasoning}
        </div>
        """)
    with mid_col.popover('Claude reasoning'):
        st.html(f"""
        <div style="font-size:16px; font-family: monospace;">
            {selected_claude_wage_check.compliance_reasoning}
        </div>
        """)
    if r_col.button(f'show citation for {selected_openai_wage_check.employee_name}', key=f'disputed_show_citation_{payroll_index}_{selected_dispute_index}'):
        #show all citation lines, from both models
        payroll_citation_line_hexes = list(set(selected_openai_wage_check.payroll_citation_lines + selected_claude_wage_check.payroll_citation_lines))
        wage_determination_citation_line_hexes = list(set(selected_openai_wage_check.wage_determination_citation_lines + selected_claude_wage_check.wage_determination_citation_lines))
        show_citation_dialog(
            wage_check=selected_openai_wage_check,
            compliance_checker=compliance_checker,
            payroll_index=payroll_index,
            employee_index=selected_dispute_index,
            disputed = True,
            payroll_citation_line_hexes_override = payroll_citation_line_hexes,
            db_wages_citation_line_hexes_override = wage_determination_citation_line_hexes,
        )

def get_tables_html(compliance_results: list[dict], failed_indices: list[int]):
    """Get HTML representation of compliance results tables."""
    tables_html = ''
    for ind, compliance_result in enumerate(compliance_results):
        tables_html+='<br><br><hr><hr><br><br>'
        if ind in failed_indices:
            tables_html += f'\n<p style = "font-size: 25px; color:red">{compliance_result["file_name"]} - FAILED TO PROCESS</p>'
            continue
        compliance_table = compliance_result['compliance_table']
        if len(compliance_table.wage_checks) == 0:
            tables_html+= f'\n<p style = "font-size: 25px;">{compliance_result["file_name"]} - NO AGREED CHECKS FOUND IN PAYROLL</p>'
        else:
            data_rows = [wage_check.model_dump() for wage_check in compliance_table.wage_checks]
            data_rows = [{key: value for key, value in row.items() if key not in ['compliance_reasoning', 'overtime_rate', 'payroll_citation_lines', 'wage_determination']}
                         for row in data_rows]
            table_df = DataFrame(data_rows)
            tables_html += f'\n<p style = "font-size: 25px;">{compliance_table.payroll_name}<br></p>' + table_df.to_html()

        if compliance_result['disputed_wage_checks']:
            dispute_table = DisputeTable(compliance_result['disputed_wage_checks'])
            disputed_df = dispute_table.get_df()
            tables_html += f'\n<p style = "font-size: 20px;"><br>Disputed Wage Checks<br></p>' + disputed_df.to_html()
    return tables_html

def get_aggrid_options(df: DataFrame, hidden_cols: list[str], cell_style_jscode: JsCode):
    """Get AgGrid grid options for a given DataFrame."""
    gb = GridOptionsBuilder.from_dataframe(dataframe=df)
    gb.configure_selection('single', use_checkbox=False)
    gb.configure_default_column(cellStyle=cell_style_jscode)
    gb.configure_auto_height(autoHeight=False)
    for hidden in hidden_cols:
        gb.configure_column(hidden, hide = True)
    return gb.build()

def render_compliance_results(cell_style_jscode: JsCode):
    """Render compliance results stored in session state."""
    if st.session_state['failed_indices']:
        st.error(
            f'Compliance check failed for {len(st.session_state['failed_indices'])} payroll files: \n{", ".join([st.session_state['compliance_results'][i]['file_name'] for i in st.session_state['failed_indices']])}')

    st.info('Compliance tables successfully loaded')
    st.markdown('### Compliance Results:')

    # get html for the table
    tables_html = get_tables_html(st.session_state['compliance_results'], st.session_state['failed_indices'])
    st.download_button(
        label = 'Download payroll compliance results as HTML',
        data = tables_html,
        file_name = 'compliance.html',
        mime = 'text/html'
    )

    if st.button('Clear Results'):
        reset_st_session_state()
        st.rerun()
    for payroll_index, compliance_result in enumerate(st.session_state['compliance_results']):
        if payroll_index in st.session_state['failed_indices']:
            continue
        compliance_checker = compliance_result['compliance_checker']

        file_name = compliance_result['file_name']

        compliance_table = compliance_result['compliance_table']

        # prepare data for aggrid
        data_list = []
        for ind, wage_check in enumerate(compliance_table.wage_checks):
            data_dict = wage_check.model_dump()
            data_dict['index'] = ind
            data_list.append(data_dict)
        data = DataFrame(data_list)
        data.drop('overtime_rate', axis=1, inplace=True, errors='ignore')
        data.drop('citation_lines', axis=1, inplace=True, errors='ignore')

        grid_options = get_aggrid_options(data, hidden_cols=['index', 'compliance_reasoning', 'payroll_citation_lines', 'wage_determination_citation_lines'], cell_style_jscode=cell_style_jscode)
        with st.expander(label=f'({file_name}) - {compliance_table.payroll_name}', expanded=False):
            st.write(f'**Project location**: {compliance_checker.project_location_str}')

            if len(data) == 0:
                if compliance_result['disputed_wage_checks']:
                    st.info('No agreed wage checks between OpenAI and Claude for this payroll.')
                else:
                    st.info('No employees found in payroll.')
            else:
                st.markdown('_Select an employee/row to view additional information (below table)_')
                # display agreed data
                grid_response = AgGrid(
                    data,
                    gridOptions=grid_options,
                    update_mode='SELECTION_CHANGED',
                    key=f'compliance_table_{payroll_index}_aggrid',
                    allow_unsafe_jscode=True,
                    height = None
                )
                selected = grid_response['selected_rows']

                if selected is not None and len(selected) > 0:
                    employee_data = selected.iloc[0]
                    employee_index = employee_data['index']
                    employee_wage_check = compliance_table.wage_checks[employee_index]
                    compliance_checker = compliance_result['compliance_checker']
                    show_employee_additional_info(employee_wage_check, compliance_checker, employee_index, payroll_index)


            #show disputed data if present
            if compliance_result['disputed_wage_checks']:
                st.divider()
                st.warning('Disputed Wage Check between OpenAI and Claude for employees: "'+'", "'.join([openai_wc.employee_name for openai_wc, claude_wc in compliance_result['disputed_wage_checks']])+'"')

                dispute_table = DisputeTable(compliance_result['disputed_wage_checks'])
                disputed_data = dispute_table.get_df()
                grid_options = get_aggrid_options(disputed_data, hidden_cols=['index'], cell_style_jscode=cell_style_jscode)
                st.markdown('## Disputed Wage Checks:')
                st.markdown('_Select an employee/row to view additional information (below table)_')
                disputed_data_response = AgGrid(
                    disputed_data,
                    gridOptions=grid_options,
                    allow_unsafe_jscode=True,
                    key=f'disputed_wage_checks_{payroll_index}_aggrid',
                    update_mode='SELECTION_CHANGED',
                    height = None,
                )
                disputed_selected = disputed_data_response['selected_rows']
                if disputed_selected is not None and len(disputed_selected) > 0:
                    selected_dispute_index = disputed_selected.iloc[0]['index']
                    selected_openai_wage_check = dispute_table.wage_checks[selected_dispute_index][0]
                    selected_claude_wage_check = dispute_table.wage_checks[selected_dispute_index][1]
                    show_disputed_employee_additional_info(
                        selected_openai_wage_check,
                        selected_claude_wage_check,
                        selected_dispute_index,
                        payroll_index,
                        dispute_table,
                        compliance_checker,
                    )

            if compliance_result['unmatched_openai']:
                st.warning('The following wage checks were found only by OpenAI:')
                for openai_wc in compliance_result['unmatched_openai']:
                    st.markdown(f'  - {openai_wc.employee_name}, Title: "{openai_wc.title}", DB Classification: "{openai_wc.davis_bacon_classification}", DB Total Rate: {openai_wc.davis_bacon_total_rate}, Paid Rate: {openai_wc.paid_rate}')
            if compliance_result['unmatched_claude']:
                st.warning('The following wage checks were found only by Claude:')
                for claude_wc in compliance_result['unmatched_claude']:
                    st.markdown(f'  - {claude_wc.employee_name}, Title: "{claude_wc.title}", DB Classification: "{claude_wc.davis_bacon_classification}", DB Total Rate: {claude_wc.davis_bacon_total_rate}, Paid Rate: {claude_wc.paid_rate}')
        st.divider()

def get_compliance_results(
        payroll_files,
        db_wages_file
):
    """Get compliance results for uploaded payroll and Davis-Bacon wages files."""
    st.success('Files uploaded successfully!')

    config_dict = load_config()
    files_save_dir = config_dict['files_save_dir']

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

    compliance_semaphore = asyncio.Semaphore(config_dict['max_concurrent_compliance_checks'])
    compliance_checkers = [
        ComplianceChecker(
            semaphore = compliance_semaphore,
            db_wages_file_path=db_wages_file_path,
            payroll_file_path = payroll_path,
            openai_compliance_matrix_prompt = openai_compliance_matrix_prompt,
            openai_single_wage_check_prompt = openai_single_wage_check_prompt,
            claude_compliance_matrix_prompt = claude_compliance_matrix_prompt,
            claude_single_wage_check_prompt = claude_single_wage_check_prompt,
            project_location_prompt = project_location_prompt,
            openai_api_key = st.secrets['openai_api_key'],
            anthropic_api_key = st.secrets['anthropic_api_key'],
            unstract_api_key = st.secrets['unstract_api_key'],
            gcloud_api_key = st.secrets['gcloud_api_key'],
            openai_model = config_dict['openai_model'],
            claude_model = config_dict['claude_model'],
            openai_files_cache_path = config_dict['openai_files_cache_path']
        )
        for payroll_path in st.session_state['payroll_files_paths']
    ]

    tasks_results = asyncio.run(asyncio.gather(
        *[checker.get_payroll_compliance_table() for checker in compliance_checkers],
        return_exceptions = True
    ))
    compliance_results = []
    failed_indices = []
    for payroll_ind in range(len(st.session_state['payroll_files_paths'])):
        file_name = payroll_files[payroll_ind].name
        if isinstance(tasks_results[payroll_ind], Exception):
            print(f'Error processing "{file_name}": \n{type(tasks_results[payroll_ind])}:{tasks_results[payroll_ind]}')
            compliance_results.append(
                {
                    'file_name': file_name,
                    'compliance_checker': compliance_checkers[payroll_ind],
                    'compliance_table': None,
                    'disputed_wage_checks': None,
                    'unmatched_openai': None,
                    'unmatched_claude': None,
                }
            )
            failed_indices.append(payroll_ind)
        else:
            compliance_table, disputed_wage_checks, unmatched_openai, unmatched_claude = tasks_results[payroll_ind]
            if compliance_table is not None:
                compliance_table = fix_table_checks(compliance_table)
            compliance_results.append(
                {
                    'file_name': file_name,
                    'compliance_checker': compliance_checkers[payroll_ind],
                    'compliance_table': compliance_table,
                    'disputed_wage_checks': disputed_wage_checks,
                    'unmatched_openai': unmatched_openai,
                    'unmatched_claude': unmatched_claude,
                }
            )
            if compliance_table is None:
                failed_indices.append(payroll_ind)
    return compliance_results, failed_indices

if 'citation_prompt' not in st.session_state:
    config_dict = load_config()
    with open(config_dict['citation_prompt_path'], 'r', encoding='utf-8') as f:
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

cell_style_jscode = JsCode("""
function(params) {
    if (params.value === 'DISPUTED') {
        return {'fontWeight': 'bold', 'color': '#cc0000'};
    } else if (params.value === '✓') {
        return {'fontWeight': 'bold', 'background-color': '#d4edda', 'color': '#33d900'};
    } else if (params.value === '✗') {
        return {'fontWeight': 'bold', 'background-color': '#f8d7da ', 'color': '#ff9900'};
    } else if (params.value === '?') {
        return {'fontWeight': 'bold', 'background-color': '#fff3cd ', 'color': '#ff9900'};
    }
    return null;
}
""")


# </editor-fold>

st.set_page_config(layout = 'wide', page_title = 'Davis-Bacon Payroll Checker', page_icon = '✅')


st.image('https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Bacon_Davis.jpg/1920px-Bacon_Davis.jpg', width=200)


st.title('Davis-Bacon Payroll Checker')
st.markdown('_AI generated results are not guaranteed to be accurate._')
st.markdown('**Uploaded files will be sent to OpenAI and Anthropic via API. Their policies (as of 11/18/25) are not to use this data to train their models. Check the [OpenAI:material/open_in_new:](https://platform.openai.com/docs/guides/your-data) and [Anthropic:material/open_in_new:](https://privacy.claude.com/en/collections/10663361-commercial-customers) privacy pages for the most recent privacy information.**')

if 'compliance_results' not in st.session_state:

    l_col, r_margin = st.columns([1, 2], gap='large')

    payroll_files = l_col.container(border= True).file_uploader('**Upload the payroll file(s)**', type = 'pdf', accept_multiple_files=True)

    db_wages_file = l_col.container(border= True).file_uploader('**Upload the Davis-Bacon wage determination file**', type = 'pdf', accept_multiple_files=False)

    if st.button('Check Payroll Compliance'):
        if payroll_files and db_wages_file:
            with st.spinner('Checking compliance (may take several minutes)...', show_time=True):
                st.session_state['compliance_results'], st.session_state['failed_indices'] = get_compliance_results(payroll_files, db_wages_file)
                # st.session_state['compliance_results'] is a list of dicts with keys:
                # 'file_name', 'compliance_checker', 'compliance_table', 'disputed_wage_checks', 'unmatched_openai', 'unmatched_claude'
            st.rerun()
        else:
            st.error('Please upload both payroll files and the Davis-Bacon wages file.')
else:
    render_compliance_results(cell_style_jscode)
if st.button("❓ Help", type = 'tertiary'):
    show_help()