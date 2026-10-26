from overlaparr.arr.client import ArrClient


class SonarrClient(ArrClient):
    def status(self) -> dict:
        return self.get("/api/v3/system/status")

    def series(self) -> list[dict]:
        return self.get("/api/v3/series")

    def episodes(self, series_id: int) -> list[dict]:
        return self.get("/api/v3/episode", seriesId=series_id)

    def episode_files(self, series_id: int) -> list[dict]:
        return self.get("/api/v3/episodefile", seriesId=series_id)

    def quality_profiles(self) -> list[dict]:
        return self.get("/api/v3/qualityprofile")

    def tags(self) -> list[dict]:
        return self.get("/api/v3/tag")
