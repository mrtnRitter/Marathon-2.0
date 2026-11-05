import shutil


def run(ctx, clips, params) -> list[dict]:
    results = []
    for clip in clips:
        try:
            shutil.rmtree(ctx.job_dir(clip), ignore_errors=False)
            result = {
                "clip_id": clip["id"],
                "status": "success",
                "output_port": "success",
                "message": "Job folder removed",
                "metadata": {},
                "files_add": [],
            }
        except FileNotFoundError:
            result = {
                "clip_id": clip["id"],
                "status": "success",
                "output_port": "success",
                "message": "Job folder already absent",
                "metadata": {},
                "files_add": [],
            }
        except Exception as error:
            result = {
                "clip_id": clip["id"],
                "status": "error",
                "output_port": "error",
                "message": str(error),
                "metadata": {},
                "files_add": [],
            }
        results.append(result)
    return results
