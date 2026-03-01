from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["dashboard"])

@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_alias(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})
