import re
import logging
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="See API", version="4.0.0")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})

from moviebox_api.v2 import Session as MbSession, Search, DownloadableSingleFilesDetail, DownloadableTVSeriesFilesDetail
from moviebox_api.v2.models import SearchResultsItem
from moviebox_api.v1.exceptions import ZeroSearchResultsError
from moviebox_api.v3.core import Homepage as V3Homepage, DownloadableVideoFilesDetail as V3Downloader, DownloadableCaptionFileDetails
from moviebox_api.v3.core import ItemDetails as V3ItemDetails, SeasonDetails as V3SeasonDetails, Search as V3Search
from moviebox_api.v3.exceptions import ZeroSearchResultsError as V3ZeroResultsError
from moviebox_api.v3.http_client import MovieBoxHttpClient
from moviebox_api.v3.models.homepage import RootHomepageModel

mb_session = MbSession()

QUALITY_PRIORITY = [1080, 720, 480, 360]

import time as _time
_home_cache: dict = {}
_HOME_TTL = 300

IMDB_TYPE_MAP = {"feature": "movie", "TV series": "tv", "movie": "movie", "tvMovie": "movie", "tvMiniSeries": "tv"}
_MB_ID_RE = re.compile(r"^\d{15,22}$")
# Strip dub/quality markers from display titles
_TITLE_TAG_RE = re.compile(r'\s*\[(?:Hindi|Tamil|Telugu|Malayalam|Kannada|Bengali|CAM|TS|HDCAM|Punjabi|Urdu|Gujarati|Marathi|CAM-?RIP|CAMRIP)[^\]]*\]', re.IGNORECASE)

def _clean_title(title: str) -> str:
    return _TITLE_TAG_RE.sub('', title).strip()


def pick_best_url(downloads: list[dict]) -> str | None:
    if not downloads:
        return None
    for q in QUALITY_PRIORITY:
        for d in downloads:
            if d.get("resolution") == q:
                return d["url"]
    return downloads[0]["url"]


def pick_best_v3_url(video_files) -> str | None:
    if not video_files:
        return None
    best = max(video_files, key=lambda f: f.resolution)
    url = str(best.resource_link)
    return "https:" + url if url.startswith("//") else url


def search_imdb(q: str) -> list:
    r = s.get(f"https://v2.sg.media-imdb.com/suggestion/titles/t/{q}.json", timeout=10)
    r.raise_for_status()
    data = r.json()
    results = []
    for item in data.get("d", []):
        img = item.get("i", {})
        media_type = IMDB_TYPE_MAP.get(item.get("q", ""), "movie")
        if media_type not in ("movie", "tv"):
            continue
        poster = img.get("imageUrl") if img else None
        results.append({
            "id": item["id"].replace("/", ""),
            "title": item.get("l", ""),
            "poster": poster,
            "backdrop": poster,
            "year": str(item.get("y", "")),
            "rating": 0,
            "media_type": media_type,
            "overview": "",
            "genres": [],
            "cast": [],
        })
    return results


def subject_to_media(subj) -> dict:
    cover_url = str(subj.cover.url) if subj.cover and subj.cover.url else None
    media_type = "movie" if subj.subject_type.value == 1 else "tv"
    try:
        raw = getattr(subj, 'imdb_rating_value', None) or getattr(subj, 'imdb_rate', None)
        rating = float(raw) if raw else 0
    except (ValueError, TypeError):
        rating = 0
    return {
        "id": subj.subject_id,
        "title": _clean_title(subj.title),
        "poster": cover_url,
        "backdrop": cover_url,
        "year": str(subj.release_date.year) if subj.release_date else "",
        "rating": rating,
        "media_type": media_type,
        "overview": getattr(subj, "description", ""),
        "genres": subj.genre if isinstance(subj.genre, list) else [],
        "cast": [],
    }


async def mb_search_title(title: str, subject_type: int | None = None) -> dict | None:
    try:
        search = Search(session=mb_session, query=title, per_page=5)
        content = await search.get_content()
        items = content.get("items", [])
        for item in items:
            if subject_type is None or item.get("subjectType") == subject_type:
                return item
        if items:
            return items[0]
    except ZeroSearchResultsError:
        pass
    return None


@app.get("/")
async def root():
    return {"status": "online", "service": "See API v4"}


@app.get("/search")
async def search(q: str = Query(...), page: int = Query(default=1)):
    try:
        async with MovieBoxHttpClient() as client:
            results = await V3Search(client_session=client, query=q, per_page=20, page=page).get_content_model()
        items = [subject_to_media(s) for s in results.items if s.cover]
        return {"results": items, "has_more": len(results.items) >= 20, "page": page}
    except Exception:
        return {"results": [], "has_more": False, "page": page}


@app.get("/home")
async def home():
    now = _time.monotonic()
    if "data" in _home_cache and now - _home_cache.get("ts", 0) < _HOME_TTL:
        return _home_cache["data"]
    try:
        async with MovieBoxHttpClient() as client:
            hp = V3Homepage(client)
            raw = await hp.get_content()
            for item in raw.get("items", []):
                subjects = [
                    s for s in item.get("subjects", [])
                    if isinstance(s.get("cover"), dict) and s["cover"].get("url")
                ]
                item["subjects"] = subjects
            page = RootHomepageModel.model_validate(raw)

        current_year = str(_time.strftime("%Y"))

        # Build a lookup: section_title -> subjects
        section_map: dict[str, list] = {sec.title: sec.subjects for sec in page.items if sec.subjects}

        def pick_section(names: list[str], max_items: int = 20) -> list:
            for name in names:
                subjects = [s for s in section_map.get(name, []) if s.cover]
                if subjects:
                    return [subject_to_media(s) for s in subjects[:max_items]]
            return []

        # Popular movies: combine Cinema + Trending, filter to current year, exclude Indian content
        MOVIE_POOL_SECTIONS = ["🔥Cinema", "🔥Trending Now", "Trending Now", "Hollywood"]
        seen_ids: set = set()
        movie_pool: list = []
        for sec_name in MOVIE_POOL_SECTIONS:
            for subj in section_map.get(sec_name, []):
                if (subj.cover
                        and subj.subject_id not in seen_ids
                        and str(subj.release_date.year) == current_year
                        and getattr(subj, "country_name", "").lower() != "india"):
                    seen_ids.add(subj.subject_id)
                    movie_pool.append(subject_to_media(subj))
        # Fallback: all current-year items regardless of country if pool is too small
        if len(movie_pool) < 8:
            seen_ids2: set = set()
            movie_pool = []
            for sec_name in MOVIE_POOL_SECTIONS:
                for subj in section_map.get(sec_name, []):
                    if subj.cover and subj.subject_id not in seen_ids2 and str(subj.release_date.year) == current_year:
                        seen_ids2.add(subj.subject_id)
                        movie_pool.append(subject_to_media(subj))
        movies = movie_pool[:20]

        series = pick_section(["Best Asian Series", "Turkish Drama", "Top Series This Week🔝"])
        animes = pick_section(["Top Anime", "Anime"])

        # Hero: first 5 from movie pool, skip CAM rips
        hero = [m for m in movies if "CAM" not in m["title"].upper()][:5] or movies[:5]

        sections = []
        if movies:
            sections.append({"name": "Filmes Populares", "type": "popular", "items": movies})
        if series:
            sections.append({"name": "Séries", "type": "tv", "items": series})
        if animes:
            sections.append({"name": "Animes", "type": "anime", "items": animes})

        result = {"sections": sections, "hero": hero}
        _home_cache["data"] = result
        _home_cache["ts"] = now
        return result
    except Exception as e:
        logger.exception("home endpoint failed")
        raise HTTPException(500, str(e))


@app.get("/details/{imdb_id}")
async def details(imdb_id: str):
    imdb_id = imdb_id.replace("/", "")
    search_results = search_imdb(imdb_id)
    if search_results:
        return search_results[0]
    return {"id": imdb_id, "title": imdb_id, "poster": None, "backdrop": None, "year": "", "rating": 0, "media_type": "movie", "overview": "", "genres": [], "cast": []}


@app.get("/categories")
async def categories():
    return {"results": [
        {"id": "action", "name": "Ação"},
        {"id": "comedy", "name": "Comédia"},
        {"id": "drama", "name": "Drama"},
        {"id": "horror", "name": "Terror"},
        {"id": "sci-fi", "name": "Ficção Científica"},
        {"id": "romance", "name": "Romance"},
        {"id": "thriller", "name": "Suspense"},
        {"id": "animation", "name": "Animação"},
    ]}


@app.get("/category/{genre_id}")
async def category(genre_id: str):
    try:
        results = search_imdb(f"{genre_id} movies")
        return {"results": (results or [])[:20]}
    except Exception as e:
        raise HTTPException(500, str(e))


class ResolveRequest(BaseModel):
    title: str
    media_type: str = "movie"
    year: str = ""


class QualityOption(BaseModel):
    resolution: int
    url: str

class StreamResponse(BaseModel):
    url: str
    subject_id: str = ""
    resource_id: str = ""
    qualities: list[QualityOption] = []


@app.post("/resolve", response_model=StreamResponse)
async def resolve_stream(req: ResolveRequest):
    st = 2 if req.media_type == "tv" else 1
    item = await mb_search_title(req.title, st)
    if not item:
        raise HTTPException(404, f"Could not find '{req.title}' on stream source")
    try:
        si = SearchResultsItem(**item)
        if st == 2:
            dl = DownloadableTVSeriesFilesDetail(session=mb_session, item=si)
            files = await dl.get_content(season=1, episode=1)
        else:
            dl = DownloadableSingleFilesDetail(session=mb_session, item=si)
            files = await dl.get_content()
        url = pick_best_url(files.get("downloads", []))
        if not url:
            raise HTTPException(404, "No streamable URL found")
        if url.startswith("//"):
            url = "https:" + url
        return StreamResponse(url=url, subject_id=str(item.get("subjectId", "")))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/stream/movie/{imdb_id}", response_model=StreamResponse)
async def movie_stream(request: Request, imdb_id: str):
    imdb_id = imdb_id.replace("/", "")

    # MovieBox v3 subject IDs are long numeric strings
    if _MB_ID_RE.match(imdb_id):
        try:
            import urllib.parse
            async with MovieBoxHttpClient() as client:
                files = await V3Downloader(client).get_content_model(imdb_id)
            best = max(files.list, key=lambda f: f.resolution) if files.list else None
            if not best:
                raise HTTPException(404, "No streamable URL found")
            qualities = []
            seen_res: set = set()
            base = str(request.base_url).rstrip("/")
            for vf in sorted(files.list, key=lambda f: f.resolution, reverse=True):
                if vf.resolution not in seen_res:
                    seen_res.add(vf.resolution)
                    raw = str(vf.resource_link)
                    if raw.startswith("//"): raw = "https:" + raw
                    qualities.append(QualityOption(resolution=vf.resolution, url=f"{base}/proxy/video?url={urllib.parse.quote(raw)}"))
            url = str(best.resource_link)
            if url.startswith("//"): url = "https:" + url
            proxy_url = f"{base}/proxy/video?url={urllib.parse.quote(url)}"
            return StreamResponse(url=proxy_url, subject_id=imdb_id, resource_id=best.resource_id, qualities=qualities)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))

    # IMDB ID flow via v2
    results = search_imdb(imdb_id)
    if not results:
        raise HTTPException(404, "Movie not found")
    title = results[0]["title"]
    item = await mb_search_title(title, 1)
    if not item:
        raise HTTPException(404, f"Could not find '{title}' on stream source")
    try:
        si = SearchResultsItem(**item)
        dl = DownloadableSingleFilesDetail(session=mb_session, item=si)
        files = await dl.get_content()
        url = pick_best_url(files.get("downloads", []))
        if not url:
            raise HTTPException(404, "No streamable URL found")
        if url.startswith("//"):
            url = "https:" + url
        import urllib.parse
        base = str(request.base_url).rstrip("/")
        proxy_url = f"{base}/proxy/video?url={urllib.parse.quote(url)}"
        return StreamResponse(url=proxy_url, subject_id=str(item.get("subjectId", "")))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/stream/tv/{imdb_id}", response_model=StreamResponse)
async def tv_stream(request: Request, imdb_id: str, season: int = Query(default=1), episode: int = Query(default=1)):
    imdb_id = imdb_id.replace("/", "")

    # MovieBox v3 subject IDs are long numeric strings
    if _MB_ID_RE.match(imdb_id):
        try:
            import urllib.parse
            all_files = []
            async with MovieBoxHttpClient() as client:
                dl = V3Downloader(client)
                async for page in dl.get_content_model_all(imdb_id):
                    all_files.extend(page.list)
                    # Stop if we already have matching episode
                    if any(f.season == season and f.episode == episode for f in all_files):
                        break
            matching = [f for f in all_files if f.season == season and f.episode == episode]
            if not matching:
                # Fallback: just use whatever is available
                matching = all_files
            if not matching:
                raise HTTPException(404, "No streamable URL found")
            best = max(matching, key=lambda f: f.resolution)
            qualities = []
            seen_res: set = set()
            base = str(request.base_url).rstrip("/")
            for vf in sorted(matching, key=lambda f: f.resolution, reverse=True):
                if vf.resolution not in seen_res:
                    seen_res.add(vf.resolution)
                    raw = str(vf.resource_link)
                    if raw.startswith("//"): raw = "https:" + raw
                    qualities.append(QualityOption(resolution=vf.resolution, url=f"{base}/proxy/video?url={urllib.parse.quote(raw)}"))
            url = str(best.resource_link)
            if url.startswith("//"): url = "https:" + url
            proxy_url = f"{base}/proxy/video?url={urllib.parse.quote(url)}"
            return StreamResponse(url=proxy_url, subject_id=imdb_id, resource_id=best.resource_id, qualities=qualities)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))

    # IMDB ID flow via v2
    results = search_imdb(imdb_id)
    if not results:
        raise HTTPException(404, "TV series not found")
    title = results[0]["title"]
    season_query = f"{title} S{season}"
    item = await mb_search_title(season_query, 2)
    if not item:
        item = await mb_search_title(title, 2)
    if not item:
        raise HTTPException(404, f"Could not find '{title}' on stream source")
    try:
        si = SearchResultsItem(**item)
        dl = DownloadableTVSeriesFilesDetail(session=mb_session, item=si)
        files = await dl.get_content(season=season, episode=episode)
        url = pick_best_url(files.get("downloads", []))
        if not url:
            raise HTTPException(404, "No streamable URL found")
        if url.startswith("//"):
            url = "https:" + url
        import urllib.parse
        base = str(request.base_url).rstrip("/")
        proxy_url = f"{base}/proxy/video?url={urllib.parse.quote(url)}"
        return StreamResponse(url=proxy_url, subject_id=str(item.get("subjectId", "")))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/captions/{subject_id}")
async def get_captions(subject_id: str, resource_id: str = Query(...)):
    try:
        async with MovieBoxHttpClient() as client:
            result = await DownloadableCaptionFileDetails(client).get_content_model(subject_id, resource_id)
        tracks = [
            {"language": cap.lan_name, "language_code": cap.lan, "url": str(cap.url)}
            for cap in result.external_captions
        ]
        return {"tracks": tracks}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/seasons/{subject_id}")
async def get_seasons(subject_id: str):
    try:
        async with MovieBoxHttpClient() as client:
            data = await V3SeasonDetails(client).get_content_model(subject_id)
        seasons = [{"season": s.se, "episodes": s.max_ep} for s in data.seasons]
        return {"seasons": seasons}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/episodes/{subject_id}/{season}")
async def get_episodes(subject_id: str, season: int):
    try:
        episode_map: dict[int, dict] = {}
        async with MovieBoxHttpClient() as client:
            dl = V3Downloader(client)
            async for page in dl.get_content_model_all(subject_id):
                for vf in page.list:
                    if vf.season == season:
                        ep = vf.episode
                        if ep not in episode_map or vf.resolution > episode_map[ep].get("resolution", 0):
                            episode_map[ep] = {"episode": ep, "title": vf.title, "duration": vf.duration, "resolution": vf.resolution}
        episodes = sorted(
            [{"episode": e["episode"], "title": e["title"], "duration": e["duration"]} for e in episode_map.values()],
            key=lambda x: x["episode"]
        )
        return {"episodes": episodes}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/details/v3/{subject_id}")
async def details_v3(subject_id: str):
    try:
        async with MovieBoxHttpClient() as client:
            d = await V3ItemDetails(client).get_content_model(subject_id)
        cover = str(d.cover.url) if d.cover else None
        media_type = "movie" if d.subject_type.value == 1 else "tv"
        try:
            rating = float(d.imdb_rating_value) if d.imdb_rating_value else 0
        except (ValueError, TypeError):
            rating = 0
        dubs = [
            {"language_code": dub.lan_code, "language": dub.lan_name, "subject_id": dub.subject_id, "original": dub.original}
            for dub in (d.dubs or [])
        ]
        staff = [
            {
                "name": s.name,
                "character": s.character,
                "staff_type": s.staff_type,
                "avatar": str(s.avatar_url) if s.avatar_url else None,
            }
            for s in (d.staff_list or [])
        ]
        return {
            "id": d.subject_id,
            "title": _clean_title(d.title),
            "overview": d.description,
            "poster": cover,
            "backdrop": cover,
            "year": str(d.release_date.year) if d.release_date else "",
            "rating": rating,
            "media_type": media_type,
            "genres": d.genre if isinstance(d.genre, list) else [],
            "runtime": d.duration,
            "duration_seconds": d.duration_seconds,
            "total_seasons": d.season_numbers,
            "country": d.country_name,
            "subtitles": d.subtitles if isinstance(d.subtitles, list) else [],
            "dubs": dubs,
            "staff": staff,
            "trailer_url": str(d.trailer.video_address.url) if d.trailer and d.trailer.video_address else None,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/proxy/video")
async def proxy_video(request: Request, url: str = Query(...)):
    vid_headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Origin": "h5.aoneroom.com",
        "Referer": "https://fmoviesunblocked.net/",
    }
    range_header = request.headers.get("range")
    if range_header:
        vid_headers["Range"] = range_header

    try:
        from fastapi.responses import StreamingResponse
        req = s.get(url, headers=vid_headers, stream=True, timeout=60)
        ct = req.headers.get("content-type", "video/mp4")
        cr = req.headers.get("content-range", "")
        cl = req.headers.get("content-length", "")
        status = req.status_code

        def gen():
            try:
                for chunk in req.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                req.close()

        hdrs = {"Accept-Ranges": "bytes"}
        if cr:
            hdrs["Content-Range"] = cr
        if cl:
            hdrs["Content-Length"] = cl

        return StreamingResponse(gen(), media_type=ct, status_code=status, headers=hdrs)
    except Exception as e:
        raise HTTPException(502, f"Proxy error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
