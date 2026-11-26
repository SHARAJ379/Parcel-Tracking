from fastapi import FastAPI

app = FastAPI(title="Parcel and Delivery Tracking Assistant")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
