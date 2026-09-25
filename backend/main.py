from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .credentials import delete_openai_key, has_openai_key, save_openai_key
from .db import init_db
from .paths import frontend_dist
from .store import create_content, get_content, list_contents


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Money Engine V3.0", lifespan=lifespan)


class ContentInput(BaseModel):
    input_source: str = Field(min_length=1, max_length=100_000)


class ApiKeyInput(BaseModel):
    value: str = Field(min_length=1, max_length=500)


def check_local_action(request: Request):
    if request.headers.get("X-Money-Engine") != "local-ui":
        raise HTTPException(403, "로컬 프로그램에서만 설정할 수 있습니다.")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "MoneyEngineV3", "phase": 1}


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
def create(payload: ContentInput):
    if not payload.input_source.strip():
        raise HTTPException(422, "소재를 입력해 주세요.")
    return create_content(payload.input_source.strip())


@app.get("/api/contents")
def contents(q: str = ""):
    return list_contents(q[:200])


@app.get("/api/contents/{content_id}")
def content(content_id: str):
    result = get_content(content_id)
    if result is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return result


FRONTEND_DIST = frontend_dist()
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        return FileResponse(FRONTEND_DIST / "index.html")
