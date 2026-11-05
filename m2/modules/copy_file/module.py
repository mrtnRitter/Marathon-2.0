import shutil


def _result(clip, status, message, files_add):
    return {
        "clip_id": clip["id"],
        "status": status,
        "output_port": status,
        "message": message,
        "metadata": {},
        "files_add": files_add,
    }


def run(ctx, clips, params) -> list[dict]:
    results = []
    for clip in clips:
        added = []
        try:
            job_name = ctx.job_name(clip)
            destination_dir = ctx.job_dir(clip)
            file_refs = clip.get("files", [])
            if not isinstance(file_refs, list):
                raise ValueError("Clip files must be a list")
            destination_dir.mkdir(parents=True, exist_ok=True)
            for file_ref in file_refs:
                source = ctx.resolve(file_ref)
                if not source.is_file():
                    raise FileNotFoundError(f"Clip file not found: {source.name}")
                destination = destination_dir / source.name
                resolved_destination = destination.resolve()
                if not resolved_destination.is_relative_to(destination_dir.resolve()):
                    raise ValueError("Copied file would escape the job folder")
                if resolved_destination != source.resolve():
                    shutil.copy2(source, destination)
                added.append(
                    {
                        "storage": ctx.storage_for(clip),
                        "path": f"{job_name}/{source.name}",
                        "role": file_ref.get("role", "restored"),
                    }
                )
            results.append(_result(clip, "success", "Files copied", added))
        except Exception as error:
            results.append(_result(clip, "error", str(error), added))
    return results
