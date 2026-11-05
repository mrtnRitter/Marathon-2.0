import random
import time


def _result(clip, status, message):
    return {
        "clip_id": clip["id"],
        "status": status,
        "output_port": status,
        "message": message,
        "metadata": {},
        "files_add": [],
    }


def run(ctx, clips, params) -> list[dict]:
    seconds = float(params.get("seconds", 1))
    fail_rate = float(params.get("fail_rate", 0))
    if seconds < 0 or not 0 <= fail_rate <= 1:
        raise ValueError("seconds must be non-negative and fail_rate must be between 0 and 1")

    ctx.log(f"Waiting for {seconds:g} seconds")
    started = time.monotonic()
    deadline = started + seconds
    ctx.progress(0 if seconds else 100)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.1, remaining))
        elapsed = time.monotonic() - started
        ctx.progress(min(100, int(elapsed / seconds * 100)))
    ctx.progress(100)

    results = []
    for clip in clips:
        if random.random() < fail_rate:
            results.append(_result(clip, "error", "Simulated failure"))
        else:
            results.append(_result(clip, "success", "Wait completed"))
    return results
