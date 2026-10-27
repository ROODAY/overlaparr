from typing import Literal

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

    def get_series(self, series_id: int) -> dict:
        return self.get(f"/api/v3/series/{series_id}")

    def get_episodes(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        return self.get("/api/v3/episode", episodeIds=list(ids))

    # --- writes: the dry-run guard runs first, before any early return ---

    def set_episodes_monitored(self, episode_ids: list[int], monitored: bool) -> None:
        self._guard("PUT", "/api/v3/episode/monitor")
        if not episode_ids:
            return
        self.put(
            "/api/v3/episode/monitor", {"episodeIds": list(episode_ids), "monitored": monitored}
        )

    def set_series_monitored(self, ids: list[int], monitored: bool) -> None:
        self._guard("PUT", "/api/v3/series/editor")
        if not ids:
            return
        self.put("/api/v3/series/editor", {"seriesIds": list(ids), "monitored": monitored})

    def delete_episode_files(self, file_ids: list[int]) -> None:
        self._guard("DELETE", "/api/v3/episodefile/bulk")
        if not file_ids:
            return
        self.delete("/api/v3/episodefile/bulk", {"episodeFileIds": list(file_ids)})

    def delete_series(self, series_id: int, add_exclusion: bool) -> None:
        self.delete(
            f"/api/v3/series/{series_id}",
            deleteFiles="true",
            addImportListExclusion="true" if add_exclusion else "false",
        )

    def add_exclusion(self, tvdb_id: int, title: str) -> int:
        res = self.post("/api/v3/importlistexclusion", {"tvdbId": tvdb_id, "title": title})
        return res["id"]

    def remove_exclusion(self, exclusion_id: int) -> None:
        self.delete(f"/api/v3/importlistexclusion/{exclusion_id}")

    def find_exclusion(self, tvdb_id: int) -> int | None:
        for ex in self.get("/api/v3/importlistexclusion"):
            if ex.get("tvdbId") == tvdb_id:
                return ex["id"]
        return None

    def edit_tags(self, ids: list[int], tag_ids: list[int], mode: Literal["add", "remove"]) -> None:
        self._guard("PUT", "/api/v3/series/editor")
        if not ids:
            return
        self.put(
            "/api/v3/series/editor",
            {"seriesIds": list(ids), "tags": list(tag_ids), "applyTags": mode},
        )

    def set_quality_profile(self, ids: list[int], profile_id: int) -> None:
        self._guard("PUT", "/api/v3/series/editor")
        if not ids:
            return
        self.put("/api/v3/series/editor", {"seriesIds": list(ids), "qualityProfileId": profile_id})

    def search_episodes(self, episode_ids: list[int]) -> None:
        self._guard("POST", "/api/v3/command")
        if not episode_ids:
            return
        self.post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": list(episode_ids)})
