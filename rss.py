from dataclasses import dataclass


@dataclass
class RSSItem:
    title: str
    link: str
    summary: str
    published: str
    published_ts: int

    @property
    def uid(self) -> str:
        core = self.link.strip() if self.link else ""
        if core:
            return core
        return f"{self.title}|{self.published}|{self.summary[:48]}"
