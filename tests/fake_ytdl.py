import os

CHUNK_BYTES = 512 * 1024


class FakeYoutubeDL:
    info = {}
    chunk_count = 1
    after_chunk = None
    downloads_started = 0

    def __init__(self, options):
        self.options = options
        self.download_dir = os.path.dirname(options["outtmpl"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        info = dict(self.info, webpage_url=url)
        if download:
            return self.process_ie_result(info, download=True)
        return info

    def process_ie_result(self, info, download):
        type(self).downloads_started += 1
        final_path = os.path.join(self.download_dir, f"{info.get('title', 'video')} [{info.get('id', 'x')}].mp4")
        part_path = final_path + ".part"
        written = 0
        with open(part_path, "wb") as handle:
            for chunk_number in range(self.chunk_count):
                handle.write(b"\0" * CHUNK_BYTES)
                handle.flush()
                written += CHUNK_BYTES
                self._report({"status": "downloading", "filename": final_path, "tmpfilename": part_path,
                              "downloaded_bytes": written, "total_bytes": None})
                if self.after_chunk:
                    self.after_chunk(chunk_number)
        os.replace(part_path, final_path)
        self._report({"status": "finished", "filename": final_path, "downloaded_bytes": written, "total_bytes": written})
        return dict(info, requested_downloads=[{"filepath": final_path}])

    def prepare_filename(self, info):
        return os.path.join(self.download_dir, f"{info.get('title', 'video')} [{info.get('id', 'x')}].NA")

    def _report(self, progress):
        for hook in self.options.get("progress_hooks", []):
            hook(progress)


def fake_factory(info, chunk_count=1, after_chunk=None):
    return type("ScriptedYoutubeDL", (FakeYoutubeDL,), {
        "info": info,
        "chunk_count": chunk_count,
        "after_chunk": staticmethod(after_chunk) if after_chunk else None,
        "downloads_started": 0,
    })
