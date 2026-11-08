"""Search scholarly metadata and walk citation links (OpenAlex, arXiv) for literature reviews.

Usage:
  python -X utf8 search_papers.py search "<query>" [--since 2024] [--sort relevance|cited|recent] [--limit 10] [--abstract]
  python -X utf8 search_papers.py arxiv  "<query>" [--limit 10] [--abstract]     # newest arXiv submissions first
  python -X utf8 search_papers.py refs   "<DOI | arXiv ID | OpenAlex ID>" [--limit 20]   # works it cites
  python -X utf8 search_papers.py cites  "<DOI | arXiv ID | OpenAlex ID>" [--since 2024] [--sort cited|recent]
  python -X utf8 search_papers.py show   "<DOI | arXiv ID | OpenAlex ID>"                 # one record with abstract

Each result shows year, venue, citation count, title, first authors and identifiers (DOI, arXiv ID,
OpenAlex ID) that can be opened to read the paper or passed to fetch_bibtex.py. OpenAlex lags arXiv by
days to weeks, so check `arxiv` for the newest preprints. Without OPENALEX_API_KEY, OpenAlex requests
share a small free daily budget per IP address. Failures are printed with the source and HTTP status.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HEADERS = {"User-Agent": "search_papers/1.0 (research helper)"}
OPENALEX = "https://api.openalex.org/works"
FIELDS = "id,doi,display_name,publication_year,cited_by_count,primary_location,authorships,type"
ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
SORTS = {"relevance": None, "cited": "cited_by_count:desc", "recent": "publication_date:desc"}


def get(url, timeout=30):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:300]
        sys.exit(f"{urllib.parse.urlsplit(url).netloc}: HTTP {error.code}: {body}")
    except urllib.error.URLError as error:
        sys.exit(f"{urllib.parse.urlsplit(url).netloc}: {error.reason}")


def openalex(path="", **params):
    params = {k: v for k, v in params.items() if v is not None}
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    return json.loads(get(OPENALEX + path + ("?" + urllib.parse.urlencode(params) if params else "")))


def abstract(work):
    index = work.get("abstract_inverted_index") or {}
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(word for _, word in words)


def arxiv_id(work):
    doi = (work.get("doi") or "").lower()
    if "10.48550/arxiv." in doi:
        return doi.split("10.48550/arxiv.", 1)[1]
    for location in [work.get("primary_location") or {}, *(work.get("locations") or [])]:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", location.get("landing_page_url") or "")
        if match:
            return match.group(1)
    return None


def show_work(n, work, with_abstract=False, full=False):
    location = work.get("primary_location") or {}
    venue = (location.get("source") or {}).get("display_name") or work.get("type") or "?"
    names = [a["author"]["display_name"] for a in work.get("authorships") or [] if a.get("author")]
    authors = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
    ids = [f"doi:{work['doi'].replace('https://doi.org/', '')}" if work.get("doi") else None,
           f"arXiv:{arxiv_id(work)}" if arxiv_id(work) else None,
           work["id"].rsplit("/", 1)[-1]]
    print(f"[{n}] {work.get('display_name')}")
    print(f"    {work.get('publication_year')} | {venue} | {work.get('cited_by_count', 0)} cites | {authors}")
    print(f"    {' | '.join(i for i in ids if i)}")
    if with_abstract:
        text = abstract(work)
        if text and not full and len(text) > 700:
            text = text[:700] + " ..."
        print(f"    {text or '(no abstract in OpenAlex)'}")


def resolve(identifier):
    identifier = identifier.strip()
    identifier = re.sub(r"^https?://(dx\.)?doi\.org/", "", identifier)
    identifier = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", identifier)
    identifier = re.sub(r"^arxiv:", "", identifier, flags=re.I)
    if re.match(r"^W\d+$", identifier, re.I):
        path = "/" + identifier.upper()
    elif re.match(r"^10\.\d{4,9}/\S+$", identifier):
        path = "/doi:" + identifier
    elif re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", identifier):
        path = "/doi:10.48550/arXiv." + re.sub(r"v\d+$", "", identifier)
    else:
        sys.exit(f"not a DOI, arXiv ID or OpenAlex ID: {identifier}. Use `search` to find it first.")
    return openalex(path, select=FIELDS + ",abstract_inverted_index,locations")


def list_works(filters, args, search=None):
    if getattr(args, "since", None):
        filters.append(f"from_publication_date:{args.since}-01-01")
    if search:  # title and abstract only: full-text matches drift off topic once sorted by citations
        filters.append("title_and_abstract.search:" + search.replace(",", " "))
    data = openalex(filter=",".join(filters) or None, sort=SORTS[args.sort], select=FIELDS +
                    (",abstract_inverted_index,locations" if args.abstract else ",locations"),
                    **{"per-page": args.limit})
    print(f"{data['meta']['count']} works match; showing {len(data['results'])}")
    for n, work in enumerate(data["results"], 1):
        show_work(n, work, args.abstract)


def search_arxiv(args):
    terms = re.findall(r'"[^"]+"|\S+', args.query)
    query = " AND ".join(f"all:{t}" for t in terms)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": args.limit})
    feed = ET.fromstring(get(url))
    entries = feed.findall("a:entry", ATOM)
    total = feed.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    print(f"{total.text if total is not None else '?'} arXiv entries match; newest {len(entries)}")
    for n, entry in enumerate(entries, 1):
        title = " ".join(entry.find("a:title", ATOM).text.split())
        names = [a.find("a:name", ATOM).text for a in entry.findall("a:author", ATOM)]
        authors = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
        ident = entry.find("a:id", ATOM).text.rsplit("/abs/", 1)[-1]
        category = entry.find("x:primary_category", ATOM)
        journal = entry.find("x:journal_ref", ATOM)
        print(f"[{n}] {title}")
        print(f"    {entry.find('a:published', ATOM).text[:10]} | {category.get('term') if category is not None else '?'}"
              f"{' | ' + journal.text if journal is not None else ''} | {authors}")
        print(f"    arXiv:{ident}")
        if args.abstract:
            text = " ".join(entry.find("a:summary", ATOM).text.split())
            print(f"    {text[:700] + (' ...' if len(text) > 700 else '')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("search", "arxiv", "refs", "cites", "show"):
        p = sub.add_parser(name)
        p.add_argument("query" if name in ("search", "arxiv") else "id")
        p.add_argument("--limit", type=int, default=20 if name in ("refs", "cites") else 10)
        p.add_argument("--abstract", action="store_true")
        if name in ("search", "cites"):
            p.add_argument("--since", type=int, help="earliest publication year")
        if name in ("search", "refs", "cites"):
            p.add_argument("--sort", choices=list(SORTS), default="relevance" if name == "search" else "cited")
    args = parser.parse_args()

    if args.command == "search":
        list_works([], args, search=args.query)
    elif args.command == "arxiv":
        search_arxiv(args)
    else:
        work = resolve(args.id)
        show_work(0, work, with_abstract=args.command == "show", full=True)
        if args.command == "refs":
            print(f"\nworks it cites (sorted by {args.sort}):")
            list_works([f"cited_by:{work['id'].rsplit('/', 1)[-1]}"], args)
        elif args.command == "cites":
            print(f"\nworks citing it (sorted by {args.sort}):")
            list_works([f"cites:{work['id'].rsplit('/', 1)[-1]}"], args)


if __name__ == "__main__":
    main()
