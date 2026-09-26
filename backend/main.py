from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .credentials import delete_openai_key, has_openai_key, save_openai_key
from .db import init_db
from .editing import edit_part
from .paths import frontend_dist, images_dir
from .pipeline import regenerate_image, run_pipeline
from .research_sources import clean_url
from .store import create_content, get_content, list_contents, recover_research_runs, reset_from, update_image, update_content


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recover_research_runs()
    yield


app = FastAPI(title="Money Engine V3.0", lifespan=lifespan)


class ContentInput(BaseModel):
    input_source: str = Field(min_length=1, max_length=100_000)


class ApiKeyInput(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class EditInput(BaseModel):
    part: str
    heading: str | None = None


class PublishInput(BaseModel):
    status: str
    published_url: str | None = None


def check_local_action(request: Request):
    if request.headers.get("X-Money-Engine") != "local-ui":
        raise HTTPException(403, "로컬 프로그램에서만 설정할 수 있습니다.")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "MoneyEngineV3", "phase": 7}


@app.post("/api/system/stop")
def stop(request: Request, background_tasks: BackgroundTasks):
    check_local_action(request)
    server = getattr(app.state, "server", None)
    if server is None:
        raise HTTPException(503, "실행 중인 서버를 찾을 수 없습니다.")
    background_tasks.add_task(setattr, server, "should_exit", True)
    return {"status": "stopping"}


@app.get("/api/settings")
def settings():
    return {"openai_key_configured": has_openai_key()}


@app.post("/api/settings/openai-key")
def set_openai_key(payload: ApiKeyInput, request: Request):
    check_local_action(request)
    try:
        save_openai_key(payload.value)
    except RuntimeError as exc:
        raise HTTPException(501, str(exc)) from exc
    return {"openai_key_configured": True}


@app.delete("/api/settings/openai-key")
def clear_openai_key(request: Request):
    check_local_action(request)
    delete_openai_key()
    return {"openai_key_configured": has_openai_key()}


@app.post("/api/contents", status_code=201)
def create(payload: ContentInput, background_tasks: BackgroundTasks):
    if not payload.input_source.strip():
        raise HTTPException(422, "소재를 입력해 주세요.")
    saved = create_content(payload.input_source.strip())
    background_tasks.add_task(run_pipeline, saved["id"])
    return saved


@app.get("/api/contents")
def contents(q: str = ""):
    return list_contents(q[:200])


@app.get("/api/contents/{content_id}")
def content(content_id: str):
    result = get_content(content_id)
    if result is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return result


@app.post("/api/contents/{content_id}/research")
def retry_research(content_id: str, request: Request, background_tasks: BackgroundTasks):
    check_local_action(request)
    saved = get_content(content_id)
    if saved is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    if saved["run"]["status"] == "RUNNING":
        raise HTTPException(409, "조사가 이미 진행 중입니다.")
    failed = next((step["step"] for step in saved["steps"] if step["status"] == "FAILED"), None)
    failed = failed or next((step["step"] for step in saved["steps"] if step["status"] == "PENDING"), None)
    if not failed:
        raise HTTPException(409, "다시 실행할 실패 단계가 없습니다.")
    reset_from(content_id, failed)
    background_tasks.add_task(run_pipeline, content_id)
    return {"status": "queued"}


@app.get("/api/contents/{content_id}/images/{slot}")
def image_file(content_id: str, slot: int, download: bool = False):
    saved = get_content(content_id)
    if saved is None or slot not in range(1, 4):
        raise HTTPException(404, "이미지를 찾을 수 없습니다.")
    item = saved["images"][slot - 1]
    if item["status"] != "COMPLETED" or not item["file_path"]:
        raise HTTPException(404, "이미지가 아직 생성되지 않았습니다.")
    path = Path(item["file_path"]).resolve()
    if path.parent != (images_dir() / content_id).resolve() or not path.is_file():
        raise HTTPException(404, "이미지 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type="image/webp", filename=item["filename"] if download else None,
                        content_disposition_type="attachment" if download else "inline")


@app.post("/api/contents/{content_id}/images/{slot}/regenerate")
def retry_image(content_id: str, slot: int, request: Request, background_tasks: BackgroundTasks):
    check_local_action(request)
    saved = get_content(content_id)
    if saved is None or slot not in range(1, 4):
        raise HTTPException(404, "이미지를 찾을 수 없습니다.")
    if saved["run"]["status"] == "RUNNING" or saved["images"][slot - 1]["status"] == "RUNNING":
        raise HTTPException(409, "이미지 작업이 진행 중입니다.")
    if not saved["body"]:
        raise HTTPException(409, "본문이 완성된 다음 생성할 수 있습니다.")
    update_image(content_id, slot, "RUNNING")
    background_tasks.add_task(regenerate_image, content_id, slot)
    return {"status": "queued"}


@app.post("/api/contents/{content_id}/edit")
async def edit_content(content_id: str, payload: EditInput, request: Request):
    check_local_action(request)
    try:
        return await edit_part(content_id, payload.part, payload.heading)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.patch("/api/contents/{content_id}/publication")
def update_publication(content_id: str, payload: PublishInput, request: Request):
    check_local_action(request)
    if get_content(content_id) is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    if payload.status not in {"DRAFT", "ACTIVE", "CLOSED", "ARCHIVE", "UPDATE"}:
        raise HTTPException(422, "콘텐츠 상태가 올바르지 않습니다.")
    try:
        url = clean_url(payload.published_url) if payload.published_url else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if payload.status == "ACTIVE" and not url:
        raise HTTPException(422, "발행한 글의 URL을 입력해 주세요.")
    update_content(content_id, status=payload.status, published_url=url)
    return get_content(content_id)


FRONTEND_DIST = frontend_dist()
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        return FileResponse(FRONTEND_DIST / "index.html")
