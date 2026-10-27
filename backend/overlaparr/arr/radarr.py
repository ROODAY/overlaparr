from typing import Literal

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

    def get_movie(self, movie_id: int) -> dict:
        return self.get(f"/api/v3/movie/{movie_id}")

    # --- writes: the dry-run guard runs first, before any early return ---

    def set_movies_monitored(self, ids: list[int], monitored: bool) -> None:
        self._guard("PUT", "/api/v3/movie/editor")
        if not ids:
            return
        self.put("/api/v3/movie/editor", {"movieIds": list(ids), "monitored": monitored})

    def delete_movie_file(self, file_id: int) -> None:
        self.delete(f"/api/v3/moviefile/{file_id}")

    def delete_movie(self, movie_id: int, add_exclusion: bool) -> None:
        self.delete(
            f"/api/v3/movie/{movie_id}",
            deleteFiles="true",
            addImportExclusion="true" if add_exclusion else "false",
        )

    def add_exclusion(self, tmdb_id: int, title: str, year: int) -> int:
        res = self.post(
            "/api/v3/exclusions", {"tmdbId": tmdb_id, "movieTitle": title, "movieYear": year}
        )
        return res["id"]

    def remove_exclusion(self, exclusion_id: int) -> None:
        self.delete(f"/api/v3/exclusions/{exclusion_id}")

    def find_exclusion(self, tmdb_id: int) -> int | None:
        for ex in self.get("/api/v3/exclusions"):
            if ex.get("tmdbId") == tmdb_id:
                return ex["id"]
        return None

    def edit_tags(self, ids: list[int], tag_ids: list[int], mode: Literal["add", "remove"]) -> None:
        self._guard("PUT", "/api/v3/movie/editor")
        if not ids:
            return
        self.put(
            "/api/v3/movie/editor",
            {"movieIds": list(ids), "tags": list(tag_ids), "applyTags": mode},
        )

    def set_quality_profile(self, ids: list[int], profile_id: int) -> None:
        self._guard("PUT", "/api/v3/movie/editor")
        if not ids:
            return
        self.put("/api/v3/movie/editor", {"movieIds": list(ids), "qualityProfileId": profile_id})

    def search(self, ids: list[int]) -> None:
        self._guard("POST", "/api/v3/command")
        if not ids:
            return
        self.post("/api/v3/command", {"name": "MoviesSearch", "movieIds": list(ids)})
