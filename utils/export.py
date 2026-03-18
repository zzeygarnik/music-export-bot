import tempfile
import os
import aiofiles


async def build_txt_file(tracks: list[dict], filename: str = "tracks.txt") -> str:
    """
    Write tracks to a temp .txt file.
    Returns the path to the file.
    Format: Исполнитель — Название
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="export_",
        delete=False,
        encoding="utf-8",
    )
    tmp_path = tmp.name
    tmp.close()

    async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
        lines = [f"{t['artist']} \u2014 {t['title']}" for t in tracks]
        await f.write("\n".join(lines))

    return tmp_path


async def cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
