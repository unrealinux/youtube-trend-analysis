"""Root entry point — delegates to app.main."""
from dotenv import load_dotenv
load_dotenv()

from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
