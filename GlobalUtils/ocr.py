from PIL import Image
from google.cloud import vision
import io
import fitz # aka PyMuPDF
import requests
import time
import httpx
import asyncio
import tomli


def derotated_load_pdf(pdf_path):
    src = fitz.open(pdf_path)
    doc = fitz.open()
    for src_page in src:  # iterate over input pages
        src_rect = src_page.rect  # source page rect
        w, h = src_rect.br  # save its width, height
        src_rot = src_page.rotation  # save source rotation
        src_page.set_rotation(0)  # set rotation to 0 temporarily
        page = doc.new_page(width=w, height=h)  # make output page
        page.show_pdf_page(  # insert source page
            page.rect,
            src,
            src_page.number,
            rotate=-src_rot,  # use reversed original rotation
        )
    return doc

def render_pdf_page_to_image(page, dpi=200):
    """
    Render a PyMuPDF page to a PIL Image.

    Args:
        page: fitz.Page object
        dpi: Resolution for rendering (default 200)

    Returns:
        PIL Image object
    """
    # Create transformation matrix for the desired DPI
    # PyMuPDF's default is 72 DPI
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    # Render page to pixmap
    pix = page.get_pixmap(matrix=mat)

    # Convert to PIL Image
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    return img

def get_doc_text_boxes(doc: fitz.Document, dpi = 300):
    client = vision.ImageAnnotatorClient()
    num_pages = len(doc)
    pages_vertices_normed = {} # page index -> list of text boxes of form {'text': box text, 'vertices': list of (x,y) tuples - box vertices normed to [0,1]]
    for page_index in range(num_pages):
        page_img = render_pdf_page_to_image(doc[page_index], dpi=dpi)
        print(f'OCRing page {page_index+1}/{num_pages}, size={page_img.size}')
        img_byte_arr = io.BytesIO()
        page_img.save(img_byte_arr, format="PNG")
        google_img = vision.Image(content=img_byte_arr.getvalue())
        ocr_response = client.text_detection(image=google_img)
        pages_vertices_normed[page_index] = [
            {
                'text': annotation.description,
                'vertices': [
                    (vertex.x/page_img.size[0], vertex.y/page_img.size[1])
                    for vertex in annotation.bounding_poly.vertices
                ]
            }
            for annotation in ocr_response.text_annotations[1:]  # skip first, it's full text
        ]
    return pages_vertices_normed

def draw_bounding_boxes(src_doc: fitz.Document, normed_bounding_boxes, color=(1, 0, 0), width=1):
    """
    Draw bounding boxes on PDF pages and save to a new file.

    Args:
        src_doc: derotated fitz.Document object
        normed_bounding_boxes: Dict mapping page numbers to lists of vertex coordinates
                       Format: {0: [[(x1,y1), (x2,y2), (x3,y3), (x4,y4)], ...], 1: [...], ...}
                       Each box is defined by 4 vertex pairs (typically top-left, top-right, bottom-right, bottom-left)
                       Coordinates should be normed to [0,1] relative to page width/height
                       Note that these coordinates are interpreted as
        color: RGB tuple with values 0-1, default red (1, 0, 0)
        width: Line width in points, default 2
    """
    out_doc = fitz.Document()
    for page_num, boxes in normed_bounding_boxes.items():
        if page_num >= len(src_doc):
            print(f"Warning: Page {page_num} doesn't exist, skipping")
            continue

        page = src_doc[page_num]
        page_width = page.rect[2]
        page_height = page.rect[3]

        for vertices in boxes:
            if len(vertices) != 4:
                print(f"Warning: Box on page {page_num} doesn't have 4 vertices, skipping")
                continue

            # Convert vertices to PyMuPDF Point objects
            points = [fitz.Point(x*page_width, y*page_height) for x, y in vertices]

            # Draw polygon connecting all vertices
            page.draw_polyline(points + [points[0]], color=color, width=width)

            out_doc.insert_pdf(src_doc, from_page = page_num, to_page = page_num)
    return out_doc

def add_invisible_text_layer(src_doc: fitz.Document, text_boxes):
    """
    Add an invisible OCR text layer to a PDF, making it searchable.

    Args:
        doc: fitz.Document object (derotated)
        text_boxes: Dict mapping page numbers to dict with:
            'vertices': normalized (in [0,1]) vertex coordinates
                             Format: {0: [[(x1,y1), (x2,y2), (x3,y3), (x4,y4)], ...], ...}
            'text': string content for each box
    Returns:
        fitz.Document object with invisible text layer added
    """
    pdfdata = src_doc.tobytes()
    doc = fitz.open("pdf", pdfdata)

    for page_index in range(len(doc)):
        page = doc.load_page(page_index)
        page_width = page.rect.width
        page_height = page.rect.height


        for text_box in text_boxes[page_index]:

            # Convert normalized coordinates to PDF points
            vertices = [(x * page_width, y * page_height) for x, y in text_box['vertices']]

            # Calculate bounding box
            xs = [v[0] for v in vertices]
            ys = [v[1] for v in vertices]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)

            box_width = x_max - x_min
            box_height = y_max - y_min

            # Calculate font size to fit the bounding box
            # This is approximate - PyMuPDF font sizing is tricky
            fontsize = box_height

            if fontsize < 1:  # Skip tiny text
                continue

            # Insert invisible text
            page.insert_text(
                (x_min, y_max),  # Bottom-left corner of text
                text = text_box['text'],
                fontsize=fontsize,
                color=(0, 0, 0),  # Color doesn't matter since it's invisible
                overlay=True,
                render_mode=3  # Invisible text mode
            )

    return doc

def google_ocr_pdf_text_overlay(input_pdf_path: str, output_pdf_path: str, dpi = 300):
    doc = derotated_load_pdf(input_pdf_path)
    pages_text_boxes = get_doc_text_boxes(doc, dpi = dpi)
    doc_with_text = add_invisible_text_layer(doc, pages_text_boxes)
    doc_with_text.save(output_pdf_path)

    doc.close()
    doc_with_text.close()

def whisper_pdf_text_extraction(
        unstract_api_key: str,
        input_pdf_path: str,
        retry_wait_step = 1., max_retry_time = 30., wait_step = 1., max_wait_time = 30.,
        return_json = False,
        add_line_nos = False
):
    creation_start_time = time.time()

    with open(input_pdf_path, 'rb') as pdf_file:
        pdf_data = pdf_file.read()
    BASE_URL = 'https://llmwhisperer-api.us-central.unstract.com/api/v2'

    while time.time() - creation_start_time < max_retry_time:

        auth_headers = {'unstract-key': unstract_api_key}
        create_params = {}
        if add_line_nos:
            create_params['add_line_nos'] = True
        create_job_response = requests.post(
            f'{BASE_URL}/whisper',
            headers=auth_headers,
            params=create_params,
            data=pdf_data
        )
        if create_job_response.status_code == 429:
            print(f"Rate limited, retrying in {retry_wait_step} seconds...")
            time.sleep(retry_wait_step)
        else:
            create_job_response.raise_for_status()
            whisper_hash = create_job_response.json()['whisper_hash']
            break
    else:
        raise TimeoutError(f"Could not create Whisper job within {max_retry_time} seconds due to rate limiting")

    status_start_time = time.time()
    complete = False
    while not complete:
        status_response = requests.get(
            BASE_URL + '/whisper-status',
            headers=auth_headers,
            params={
                'whisper_hash': whisper_hash
            }
        )
        if status_response.json()['status'] == 'error':
            raise RuntimeError(f"Whisper job failed: {status_response.json()}")
        elif status_response.json()['status'] == 'processed':
            complete = True
        elif time.time() - status_start_time > max_wait_time:
            raise TimeoutError(f"Whisper job did not complete within {max_wait_time} seconds")
        else:
            time.sleep(wait_step)
    result_response = requests.get(
        BASE_URL + '/whisper-retrieve',
        headers=auth_headers,
        params={
            'whisper_hash': whisper_hash
        }
    )

    if return_json:
        return result_response.json()

    return result_response.json()['result_text']


async def async_whisper_pdf_text_extraction(
        unstract_api_key: str,
        input_pdf_path: str,
        retry_wait_step=1.,
        max_retry_time=30.,
        wait_step=1.,
        max_wait_time=30.,
        return_json=False,
        add_line_nos = False
):
    creation_start_time = time.time()

    with open(input_pdf_path, 'rb') as pdf_file:
        pdf_data = pdf_file.read()

    BASE_URL = 'https://llmwhisperer-api.us-central.unstract.com/api/v2'
    auth_headers = {'unstract-key': unstract_api_key}
    create_params = {}
    if add_line_nos:
        create_params['add_line_nos'] = True


    async with httpx.AsyncClient() as client:
        # Retry loop for job creation with rate limit handling
        while time.time() - creation_start_time < max_retry_time:
            create_job_response = await client.post(
                f'{BASE_URL}/whisper',
                headers=auth_headers,
                params=create_params,
                content=pdf_data
            )

            if create_job_response.status_code == 429:
                print(f"Rate limited, retrying in {retry_wait_step} seconds...")
                await asyncio.sleep(retry_wait_step)
            else:
                create_job_response.raise_for_status()
                whisper_hash = create_job_response.json()['whisper_hash']
                break
        else:
            raise TimeoutError(f"Could not create Whisper job within {max_retry_time} seconds due to rate limiting")

        # Status polling loop
        status_start_time = time.time()
        complete = False

        while not complete:
            status_response = await client.get(
                f'{BASE_URL}/whisper-status',
                headers=auth_headers,
                params={'whisper_hash': whisper_hash}
            )

            if status_response.json()['status'] == 'error':
                raise RuntimeError(f"Whisper job failed: {status_response.json()}")
            elif status_response.json()['status'] == 'processed':
                complete = True
            elif time.time() - status_start_time > max_wait_time:
                raise TimeoutError(f"Whisper job did not complete within {max_wait_time} seconds")
            else:
                await asyncio.sleep(wait_step)

        # Retrieve results
        result_response = await client.get(
            f'{BASE_URL}/whisper-retrieve',
            headers=auth_headers,
            params={'whisper_hash': whisper_hash}
        )

        if return_json:
            return result_response.json()

        return result_response.json()['result_text']

if __name__ == "__main__":
    pdf_path = r"C:\Users\jaeckle\PycharmProjects\DavisBaconApp\documents\coulson.pdf"
    with open('config.toml', 'rb') as f:
        config = tomli.load(f)
    unstract_api_key = config['unstract_api_key']

    unstract_json = asyncio.run(async_whisper_pdf_text_extraction(
        unstract_api_key=unstract_api_key,
        input_pdf_path=pdf_path,
        return_json=True
    ))
    pass