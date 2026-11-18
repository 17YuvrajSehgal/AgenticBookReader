"""Book file -> one text string per page. Swap this module to change the parser."""
import pymupdf


def parse_pdf(path: str) -> list[str]:
    # ponytail: text layer only. Scanned books and good formula LaTeX need Marker/MinerU (design 5.2).
    with pymupdf.open(path) as doc:
        # Postgres text cannot hold NUL bytes, and some PDFs have them.
        return [page.get_text().replace("\x00", "") for page in doc]


# When is a page "a page with a figure"? Tune these on real books.
FIGURE_MIN_AREA = 0.05      # a picture that covers at least 5% of the page (skips small icons)
FIGURE_MIN_DRAWINGS = 40    # or this many vector shapes (diagrams drawn with lines and boxes)
FIGURE_DPI = 100            # about 1,300 LLM input tokens for a normal book page


def page_image(path: str, page_no: int) -> bytes | None:
    """PNG of the page if it has a figure the text layer cannot show, else None."""
    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        min_area = FIGURE_MIN_AREA * page.rect.get_area()
        has_picture = any(rect.get_area() >= min_area
                          for img in page.get_images() for rect in page.get_image_rects(img[0]))
        if not has_picture and len(page.get_drawings()) < FIGURE_MIN_DRAWINGS:
            return None
        return page.get_pixmap(dpi=FIGURE_DPI).tobytes("png")


def toc(path: str) -> list:
    """The book's own table of contents: [[level, title, start_page], ...]. Empty if it has none."""
    with pymupdf.open(path) as doc:
        return doc.get_toc()
