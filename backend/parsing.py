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


# The app's highlight colours as PDF colours (RGB, 0 to 1). Same colours as .hl.* in app.css.
INKS = {"yellow": (1.0, 0.882, 0.302), "green": (0.557, 0.898, 0.608),
        "pink": (1.0, 0.639, 0.769), "blue": (0.549, 0.784, 1.0)}


def _words_in(page, rect) -> str:
    """The words whose middle lies inside rect. Reading the whole rect would also catch the next line."""
    inside = [w for w in page.get_text("words")
              if rect.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
    return " ".join(w[4] for w in inside)


def take_highlights(path: str) -> list[dict]:
    """Highlights saved inside the PDF by another tool (Edge, Acrobat, ...), taken out of this file.

    Returns [{page, color, text, rects}] in the app's format: rects as [left, top, width, height], each a part
    of the page size. The caller saves them as app highlights; this file then no longer draws them, so a
    highlight is never shown twice and the reader can remove it in the app.
    """
    found = []
    with pymupdf.open(path) as doc:
        for page in doc:
            w, h = page.rect.width, page.rect.height
            for annot in list(page.annots(types=[pymupdf.PDF_ANNOT_HIGHLIGHT])):
                v = annot.vertices or []
                quads = [pymupdf.Quad(v[i:i + 4]).rect for i in range(0, len(v) - 3, 4)] or [annot.rect]
                rgb = (annot.colors.get("stroke") or [1, 1, 0])[:3]
                color = min(INKS, key=lambda name: sum((a - b) ** 2 for a, b in zip(INKS[name], rgb)))
                text = " ".join(t for t in (_words_in(page, r) for r in quads) if t)
                found.append({"page": page.number + 1, "color": color, "text": text[:2000] or "(highlight)",
                              "rects": [[round(r.x0 / w, 4), round(r.y0 / h, 4), round(r.width / w, 4),
                                         round(r.height / h, 4)] for r in quads]})
                page.delete_annot(annot)  # also removes its comment popup
        if found:
            # Saved next to the file, then moved over it by the caller (storage.put), so a crash halfway never
            # leaves a broken book.
            doc.save(path + ".new", garbage=1)
    return found


def with_highlights(path: str, highlights: list[dict]) -> bytes:
    """A copy of the PDF with the app's highlights written in as normal PDF highlights, for other tools."""
    with pymupdf.open(path) as doc:
        for h in highlights:
            page = doc[h["page_no"] - 1]
            w, h_ = page.rect.width, page.rect.height
            quads = [pymupdf.Rect(l * w, t * h_, (l + rw) * w, (t + rh) * h_).quad for l, t, rw, rh in h["rects"]]
            annot = page.add_highlight_annot(quads=quads)
            annot.set_colors(stroke=INKS.get(h["color"], INKS["yellow"]))
            annot.set_info(title="Reading Companion")  # no comment text: Edge would show a note bubble on each
            annot.update()
        return doc.tobytes(garbage=1, deflate=True)


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

    # Highlights: one made by "another tool" comes out as an app highlight, then goes back in.
    import os
    import tempfile
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Keep this line marked.", fontsize=12)
    page.insert_text((72, 130), "Not this one.", fontsize=12)
    mark = page.add_highlight_annot(page.search_for("Keep this line marked."))
    mark.set_colors(stroke=(0.5, 0.9, 0.6))
    mark.update()
    path = os.path.join(tempfile.mkdtemp(), "t.pdf")
    doc.save(path)
    got = take_highlights(path)
    assert len(got) == 1 and got[0]["color"] == "green" and got[0]["text"] == "Keep this line marked.", got
    with pymupdf.open(path + ".new") as left:
        assert not list(left[0].annots()), "the highlight must be gone from the file"
    out = pymupdf.open(stream=with_highlights(path + ".new", [{**got[0], "page_no": 1}]))
    out_page = out[0]  # kept: annotations of a page object that is gone cannot be read
    back = list(out_page.annots())
    assert len(back) == 1 and back[0].type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT
    assert abs(back[0].rect.x0 - mark.rect.x0) < 1 and abs(back[0].rect.y1 - mark.rect.y1) < 1
    print("highlights ok")
