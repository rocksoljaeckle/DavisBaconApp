import sys
from PIL import ImageDraw
from fuzzysearch import find_near_matches
import tomli
import fitz
from PIL import Image
import io
from pydantic import BaseModel
from openai import OpenAI, AsyncOpenAI
import uuid
import os
import asyncio

from GlobalUtils.ocr import whisper_pdf_text_extraction

class CitationLines(BaseModel):
    lines: list[int]

def render_line_highlights(
        text: str,
        highlight_lines: list[int],
        highlight_color: tuple = (1, 1, 0),
        highlight_opacity: float = 0.3,
        zoom: float = 2.0,
        line_height: int = 16,
        text_height: int = 8,
        page_height: int | None = None
):
    doc = fitz.open()  # new empty PDF
    if page_height is not None:
        page = doc.new_page(height=page_height)  # new page
    else:
        page = doc.new_page()  # new page
    y = 0
    for line_ind, line in enumerate(text.splitlines()):
        highlight_rect = fitz.Rect(0, y, page.rect.width, y + line_height)
        text_rect = fitz.Rect(50, y + 2, 550, y + line_height)
        while page.insert_textbox(text_rect, line, fontsize=text_height) < 0.:
            text_rect.y1 += line_height
            highlight_rect.y1 += line_height
        if line_ind in highlight_lines:
            annot = page.add_highlight_annot(highlight_rect)
            annot.set_colors(stroke=highlight_color)
            annot.set_opacity(highlight_opacity)
            annot.update()
        y = highlight_rect.y1  # increment y position for next line
    if y > page.rect.height:
        return render_line_highlights(text, highlight_lines, highlight_color, highlight_opacity, zoom, line_height, text_height, page_height=y+20)
    mat = fitz.Matrix(zoom, zoom)
    pix_bytes = page.get_pixmap(matrix = mat).tobytes('png')
    img = Image.open(io.BytesIO(pix_bytes))
    return img.convert('RGB')

def render_pdf_page_with_highlights(
        pdf_source: str | bytes,
        page: int,
        bboxes: list[list[float]],
        highlight_color: tuple[float, float, float] = (1, 1, 0),  # RGB 0-1, default yellow
        highlight_opacity: float = 0.3,
        zoom: float = 2.0  # Higher = better quality, 2.0 is good default
) -> Image.Image:
    """
    Render a PDF page with a highlighted bounding box.

    Args:
        pdf_source: Path to PDF file, or PDF bytes
        page: Page number (0-indexed)
        bbox: Bounding box as [x0, y0, x1, y1] in PDF coordinates
        highlight_color: RGB tuple with values 0-1
        highlight_opacity: Transparency of highlight (0-1)
        zoom: Rendering resolution multiplier

    Returns:
        PIL Image with highlighted region
    """
    if isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source)
    elif isinstance(pdf_source, str):
        doc = fitz.open(pdf_source)
    else:
        raise ValueError('pdf_source must be str path or bytes')

    if page < 0 or page >= len(doc):
        raise ValueError(f"Page {page} out of range. PDF has {len(doc)} pages.")

    pdf_page = doc[page]

    # Render page at higher resolution
    mat = fitz.Matrix(zoom, zoom)
    pix = pdf_page.get_pixmap(matrix=mat)

    # Convert to PIL Image
    img_data = pix.tobytes("png")
    img = Image.open(io.BytesIO(img_data))

    # Create a semi-transparent overlay
    overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw highlight rectangles
    for bbox in bboxes:
        x0, y0, x1, y1 = bbox
        scaled_bbox = [x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom]


        # Convert RGB 0-1 to 0-255
        color_255 = tuple(int(c * 255) for c in highlight_color)
        alpha = int(highlight_opacity * 255)
        fill_color = (*color_255, alpha)

        # Draw filled rectangle
        draw.rectangle(scaled_bbox, fill=fill_color, width=3)

    # Composite the overlay onto the original image
    img = img.convert('RGBA')
    img = Image.alpha_composite(img, overlay)

    doc.close()

    return img.convert('RGB')  # Convert back to RGB for st.image

def find_best_fuzzy_lines(text: str, query: str, max_l_dist: int | None = None):
    """
    Find the best fuzzy match for `query` in `text` and return the
    start and end line numbers (0-indexed) spanned by that match.

    Args:
        text: The full document text.
        query: The search term or phrase.
        max_l_dist: Max allowed Levenshtein distance (default=2).

    Returns:
        (start_line, end_line, match_text) or None if not found.
    """
    if max_l_dist is None:
        max_l_dist = len(query) // 5  # 20% of query length, min 2

    matches = find_near_matches(query, text, max_l_dist=max_l_dist)
    if not matches:
        return None

    # Pick the best (lowest distance, tie-breaker = longest overlap)
    best = min(matches, key=lambda m: (m.dist, -len(m.matched)))

    start_line = text[:best.start].count('\n')
    end_line = text[:best.end].count('\n')

    return range(start_line, end_line)


async def find_best_openai_lines(
        text: str,
        query: str,
        citation_prompt: str,
        openai_client: AsyncOpenAI,
        openai_model: str = 'gpt-5'
):
    marked_lines =  ''
    for line_ind, line in enumerate(text.splitlines(keepends=True)):
        marked_lines += f'<<LINE {line_ind}>> {line}'
    lineate_input = [
        {
            'role': 'system',
            'content': citation_prompt
        },
        {
            'role': 'user',
            'content': f'Document:\n{marked_lines}\n\nQuery: "{query}"'
        }
    ]
    response = await openai_client.responses.parse(
        model=openai_model,
        input=lineate_input,
        text_format = CitationLines
    )
    lines = response.output_parsed.lines
    return lines


async def find_citation_bboxes_normed(
        unstract_response_json: dict,
        citation_query: str,
        citation_prompt: str,
        openai_client: AsyncOpenAI
):
    """
    Find citation bounding boxes in normalized coordinates (0-1) for a given citation query.
    Args:
        unstract_response_json: JSON response from Unstract OCR
        citation_query: Citation text to search for
        openai_client: AsyncOpenAI client instance
    Returns:
        List of dicts with 'page' and 'bbox' keys in normalized coordinates (0-1), or None if not found
        """
    document_text = unstract_response_json['result_text']
    document_text_no_page_breaks = ''
    for line in document_text.splitlines(keepends=True):
        if line.strip() != '<<<':
            document_text_no_page_breaks += line
    lines = await find_best_openai_lines(
        document_text_no_page_breaks,
        citation_query,
        citation_prompt,
        openai_client
    )
    if lines is None:
        return None

    line_whisper_boxes = [unstract_response_json['line_metadata'][line_ind] for line_ind in lines]

    line_boxes = [
        {
            'page': line_box[0],
            'bbox': [0.01, (line_box[1]-line_box[2])/line_box[3], 0.99, (line_box[1])/line_box[3]]
        }
        for line_box in line_whisper_boxes
    ]
    return line_boxes # normed to (0,1)

def render_pdf_bboxes_to_images(
        citation_bboxes: list[dict],
        pdf_source: str | bytes,
):
    """
    Generate images for each page in source document, with  citation bounding boxes highlighted.

    Args:
        citation_bboxes: List of dicts with 'page' and 'bbox' keys in normalized coordinates (0-1)
        pdf_source: Path to PDF file, or PDF bytes
    Returns:
        images, page_numbers - List of PIL Images with highlighted regions, page number for each image
    """
    if isinstance(pdf_source, bytes):
        fitz_doc = fitz.Document(stream=pdf_source)
    elif isinstance(pdf_source, str):
        fitz_doc = fitz.Document(pdf_source)
    else:
        raise ValueError('pdf_source must be str path or bytes')
    pages_dims = [fitz_doc[i].rect for i in range(len(fitz_doc))]
    fitz_doc.close()

    page_bboxes = {}
    for citation in citation_bboxes:
        page = citation['page']
        bbox_normed = citation['bbox']
        bbox = [
            bbox_normed[0] * pages_dims[page].width,
            bbox_normed[1] * pages_dims[page].height,
            bbox_normed[2] * pages_dims[page].width,
            bbox_normed[3] * pages_dims[page].height,
        ]
        if page not in page_bboxes:
            page_bboxes[page] = []
        page_bboxes[page].append(bbox)
    images = []
    page_numbers = []
    for page, bboxes in page_bboxes.items():
        img = render_pdf_page_with_highlights(
            pdf_source=pdf_source,
            page=page,
            bboxes=bboxes
        )
        images.append(img)
        page_numbers.append(page)
    return images, page_numbers


async def get_unstract_citation_images(
        pdf_source: str | bytes,
        unstract_response_json: dict,
        citation_query: str,
        citation_prompt: str,
        openai_client: AsyncOpenAI,
        return_page_numbers: bool = False
):
    """
    Generate images for each citation bounding box.

    Args:
        pdf_source: Path to PDF file, or PDF bytes
        unstract_response_json: JSON response from Unstract OCR
        citation_query: Citation text to search for
        citation_prompt: LLM prompt for finding citation lines
        openai_client: AsyncOpenAI client instance
        return_page_numbers: Whether to return page numbers along with images
    Returns:
        List of PIL Images with highlighted regions, or (images (list), page_numbers (list)) tuple if return_page_numbers is True
    """
    citation_bboxes = await find_citation_bboxes_normed(
        unstract_response_json=unstract_response_json,
        citation_query=citation_query,
        citation_prompt = citation_prompt,
        openai_client=openai_client
    )
    if citation_bboxes is None:
        return None

    images, page_numbers = render_pdf_bboxes_to_images(
        citation_bboxes=citation_bboxes,
        pdf_source=pdf_source
    )
    if return_page_numbers:
        return images, page_numbers
    return images

async def main():
    with open('config.toml', 'rb') as f:
        config = tomli.load(f)
    unstract_api_key = config['unstract_api_key']
    openai_api_key = config['openai_api_key']

    with open('prompts/citation_prompt.md', 'r', encoding='utf-8') as f:
        citation_prompt = f.read()

    PDF_PATH = r"C:\Users\jaeckle\Downloads\coulson-rotated.pdf"

    unstract_response_json = whisper_pdf_text_extraction(
        unstract_api_key=unstract_api_key,
        input_pdf_path=PDF_PATH,
        return_json=True
    )

    openai_client = AsyncOpenAI(api_key=openai_api_key)

    test_citation_query = 'Name: Allan L, Paid Rate: 33.00'
    images = await get_unstract_citation_images(
        pdf_source=PDF_PATH,
        unstract_response_json=unstract_response_json,
        citation_query=test_citation_query,
        citation_prompt = citation_prompt,
        openai_client=openai_client
    )

    save_dir = f'tests/citation_images_{uuid.uuid4().hex[:5]}'
    os.makedirs(save_dir, exist_ok=True)
    for i, img in enumerate(images):
        img.save(os.path.join(save_dir,f'citation_{i}.png'))
    print(f'Saved citation images to {save_dir}')


if __name__ == "__main__":

    asyncio.run(main())
    pass