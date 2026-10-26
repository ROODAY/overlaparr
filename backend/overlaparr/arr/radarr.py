from overlaparr.arr.client import ArrClient


class RadarrClient(ArrClient):
    def status(self) -> dict:
        return self.get("/api/v3/system/status")

    def movies(self) -> list[dict]:
        return self.get("/api/v3/movie")

    def quality_profiles(self) -> list[dict]:
        return self.get("/api/v3/qualityprofile")

    def tags(self) -> list[dict]:
        return self.get("/api/v3/tag")
