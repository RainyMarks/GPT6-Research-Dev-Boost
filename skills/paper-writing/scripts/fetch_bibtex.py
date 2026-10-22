"""Fetch BibTeX for papers from Crossref/doi.org, DBLP or arXiv, and show where each entry came from.

Usage: python -X utf8 fetch_bibtex.py "<title | DOI | arXiv ID>" ["<another>" ...]

For every query it prints the source URL, the matched title with its similarity to the
query, and the BibTeX. Titles that do not closely match are flagged for a manual check;
queries that find nothing are reported as NOT FOUND, so the citation can be marked
unverified. Only public APIs are read; network failures are reported, never worked around.
"""
import difflib
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HEADERS = {"User-Agent": "fetch_bibtex/1.0 (research helper)"}
ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
GOOD_MATCH = 0.9


def get(url, accept=None, timeout=20):
    headers = dict(HEADERS, **({"Accept": accept} if accept else {}))
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def similarity(a, b):
    def norm(s):
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


MONTHS = {m: m[:3].lower() for m in ("January", "February", "March", "April", "May", "June", "July",
                                      "August", "September", "October", "November", "December")}


def from_doi(doi):
    url = "https://doi.org/" + doi
    bib = get(url, accept="application/x-bibtex").strip()
    bib = re.sub(r"pages=\{(\w+)\s*[–—-]\s*(\w+)\}", r"pages={\1--\2}", bib)
    bib = re.sub(r"month=([A-Za-z]+)", lambda m: "month=" + MONTHS.get(m.group(1).capitalize(), m.group(1).lower()[:3]), bib)
    title = re.search(r"title\s*=\s*\{(.+?)\}\s*,", bib)
    return url, title.group(1) if title else "", bib


def from_arxiv_id(arxiv_id):
    entry = ET.fromstring(get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")).find("a:entry", ATOM)
    if entry is None or entry.find("a:title", ATOM) is None:
        return None
    title = " ".join(entry.find("a:title", ATOM).text.split())
    doi = entry.find("x:doi", ATOM)
    if doi is not None and doi.text:  # arXiv knows the published version
        return from_doi(doi.text.strip())
    authors = [a.find("a:name", ATOM).text for a in entry.findall("a:author", ATOM)]
    year = entry.find("a:published", ATOM).text[:4]
    key = re.sub(r"\W", "", authors[0].split()[-1]).lower() + year
    bib = (f"@misc{{{key},\n  title = {{{title}}},\n  author = {{{' and '.join(authors)}}},\n  year = {{{year}}},\n"
           f"  eprint = {{{arxiv_id}}},\n  archivePrefix = {{arXiv}},\n  url = {{https://arxiv.org/abs/{arxiv_id}}}\n}}")
    return f"https://arxiv.org/abs/{arxiv_id}", title, bib


def search_crossref(title):
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query.bibliographic": title, "rows": 5})
    items = [it for it in json.loads(get(url))["message"]["items"] if it.get("title")]
    best = max(items, key=lambda it: similarity(title, it["title"][0]), default=None)
    if best and similarity(title, best["title"][0]) >= GOOD_MATCH:
        return from_doi(best["DOI"])
    return None


def search_dblp(title):
    url = "https://dblp.org/search/publ/api?" + urllib.parse.urlencode({"q": title, "format": "json", "h": 5})
    hits = json.loads(get(url, timeout=10))["result"]["hits"].get("hit", [])
    best = max(hits, key=lambda h: similarity(title, h["info"]["title"]), default=None)
    if best and similarity(title, best["info"]["title"]) >= GOOD_MATCH:
        bib_url = f"https://dblp.org/rec/{best['info']['key']}.bib"
        return bib_url, best["info"]["title"], get(bib_url, timeout=10).strip()
    return None


def search_arxiv(title):
    query = urllib.parse.urlencode({"search_query": f'ti:"{title}"', "max_results": 5})
    entries = ET.fromstring(get("https://export.arxiv.org/api/query?" + query)).findall("a:entry", ATOM)
    scored = [(similarity(title, " ".join(e.find("a:title", ATOM).text.split())), e) for e in entries]
    score, best = max(scored, key=lambda pair: pair[0], default=(0, None))
    if best is not None and score >= GOOD_MATCH:
        return from_arxiv_id(best.find("a:id", ATOM).text.rsplit("/abs/", 1)[-1].split("v")[0])
    return None


def lookup(query):
    query = query.strip()
    if re.match(r"^10\.\d{4,9}/\S+$", query):
        return from_doi(query), []
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", query):
        return from_arxiv_id(query.split("v")[0]), []
    problems = []
    for name, search in (("crossref", search_crossref), ("dblp", search_dblp), ("arxiv", search_arxiv)):
        try:
            found = search(query)
        except Exception as error:  # report the source as unavailable and try the next one
            problems.append(f"{name}: {type(error).__name__}")
            continue
        if found:
            return found, problems
        problems.append(f"{name}: no close title match")
    return None, problems


def main(queries):
    missing = 0
    for query in queries:
        print(f"% query: {query}")
        try:
            found, problems = lookup(query)
        except Exception as error:
            found, problems = None, [f"{type(error).__name__}: {error}"]
        if not found:
            missing += 1
            print(f"% NOT FOUND ({'; '.join(problems) or 'no result'}). Mark this citation as unverified.\n")
            continue
        source, title, bib = found
        print(f"% source: {source}")
        score = similarity(query, title) if not re.match(r"^(10\.|\d{4}\.)", query) else 1.0
        print(f"% matched title: {title} (similarity {score:.2f})")
        if score < GOOD_MATCH:
            print("% WARNING: the matched title differs from the query; check it by hand.")
        print(bib + "\n")
    return 1 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
