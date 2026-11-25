"""Book file -> one text string per page. Swap this module to change the parser."""
import re

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


def _has_figure(page) -> bool:
    min_area = FIGURE_MIN_AREA * page.rect.get_area()
    has_picture = any(rect.get_area() >= min_area
                      for img in page.get_images() for rect in page.get_image_rects(img[0]))
    return has_picture or len(page.get_drawings()) >= FIGURE_MIN_DRAWINGS


def page_image(path: str, page_no: int) -> bytes | None:
    """PNG of the page if it has a figure the text layer cannot show, else None."""
    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        return page.get_pixmap(dpi=FIGURE_DPI).tobytes("png") if _has_figure(page) else None


def figure_pages(path: str, start: int, end: int) -> set[int]:
    """The pages from start to end that have a figure. Nothing is drawn, so this is fast."""
    with pymupdf.open(path) as doc:
        return {n for n in range(start, end + 1) if _has_figure(doc[n - 1])}


def toc(path: str) -> list:
    """The book's own table of contents: [[level, title, start_page], ...]. Empty if it has none."""
    with pymupdf.open(path) as doc:
        return doc.get_toc()


def chapters(entries: list, page_count: int) -> list[dict]:
    """The book cut into chapters: [{title, start, end}], in order, covering every page once.

    Notes, chats and highlights are grouped by these. Some books put chapters under "Part I" entries,
    so the chapter level is the highest level that has a title starting with "Chapter"; else level 1.
    """
    entries = [(level, title.strip(), page) for level, title, page in entries if 1 <= page <= page_count]
    cut = min((level for level, title, _ in entries if re.match(r"chapter\b", title, re.I)), default=1)
    tops = [(title, page) for level, title, page in entries if level <= cut]
    if not tops:
        return [{"title": "Whole book", "start": 1, "end": page_count}]
    if tops[0][1] > 1:
        tops.insert(0, ("Start of the book", 1))
    out = []
    for (title, start), (_, next_start) in zip(tops, tops[1:] + [("", page_count + 1)]):
        if next_start > start:  # a part that starts on the same page as its first chapter is only a wrapper
            out.append({"title": title, "start": start, "end": next_start - 1})
    return out


if __name__ == "__main__":
    # Self-check. Run: python parsing.py
    book = [[1, "Cover", 1], [1, "Part I", 5], [2, "Chapter 1. A", 5], [3, "Section", 6], [2, "Chapter 2. B", 9],
            [1, "Index", 20]]
    assert chapters(book, 22) == [
        {"title": "Cover", "start": 1, "end": 4}, {"title": "Chapter 1. A", "start": 5, "end": 8},
        {"title": "Chapter 2. B", "start": 9, "end": 19}, {"title": "Index", "start": 20, "end": 22}], chapters(book, 22)
    assert chapters([], 7) == [{"title": "Whole book", "start": 1, "end": 7}]
    assert chapters([[1, "Rules", 3]], 7)[0] == {"title": "Start of the book", "start": 1, "end": 2}
    print("chapters ok")
