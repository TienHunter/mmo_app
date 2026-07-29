from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile

from .database import Database


HEADERS = [
    "Kênh YouTube",
    "Kênh TikTok",
    "Link video",
    "Title",
    "Path video đã tách",
    "Có upload lên TikTok không",
    "Upload thành công không",
    "Log",
]


@dataclass(frozen=True)
class PipelineReportRow:
    youtube_channel: str
    youtube_channel_url: str
    tiktok_channel: str
    video_link: str
    title: str
    rendered_video_path: str
    has_tiktok_upload: str
    upload_success: str
    log: str

    def as_excel_row(self) -> list[str]:
        return [
            self.youtube_channel,
            self.tiktok_channel,
            self.video_link,
            self.title,
            self.rendered_video_path,
            self.has_tiktok_upload,
            self.upload_success,
            self.log,
        ]


def build_pipeline_report_rows(database: Database) -> list[PipelineReportRow]:
    with database.connect() as connection:
        media_items = connection.execute(
            """SELECT m.*, c.title channel_title, c.source_url channel_source_url
               FROM media_items m
               LEFT JOIN channels c ON c.channel_id=m.channel_id
               ORDER BY m.id DESC"""
        ).fetchall()
        rows: list[PipelineReportRow] = []
        for media in media_items:
            mapped_account = connection.execute(
                """SELECT a.name
                   FROM channel_route_sources s
                   JOIN channel_routes r ON r.id=s.route_id AND r.active=1
                   JOIN tiktok_accounts a ON a.id=r.account_id AND a.active=1
                   WHERE s.channel_id=?
                   ORDER BY r.id LIMIT 1""",
                (media["channel_id"],),
            ).fetchone()
            artifacts = connection.execute(
                """SELECT * FROM artifacts
                   WHERE media_item_id=? AND kind='rendered'
                   ORDER BY COALESCE(segment_index,1), id""",
                (media["id"],),
            ).fetchall()
            if not artifacts:
                rows.append(_row_without_rendered_artifact(connection, media, mapped_account))
                continue
            for artifact in artifacts:
                tasks = connection.execute(
                    """SELECT u.*, a.name account_name
                       FROM upload_tasks u
                       LEFT JOIN tiktok_accounts a ON a.id=u.account_id
                       WHERE u.artifact_id=?
                       ORDER BY u.id""",
                    (artifact["id"],),
                ).fetchall()
                if not tasks:
                    rows.append(_row_without_upload_task(connection, media, mapped_account))
                    continue
                for task in tasks:
                    log = _upload_task_log(connection, media["id"], task["id"], task["error_message"])
                    rows.append(
                        PipelineReportRow(
                            youtube_channel=_youtube_channel_name(media),
                            youtube_channel_url=_youtube_channel_url(media),
                            tiktok_channel=task["account_name"] or (mapped_account["name"] if mapped_account else ""),
                            video_link=media["source_url"] or f"https://www.youtube.com/watch?v={media['youtube_video_id']}",
                            title=media["title"] or "",
                            rendered_video_path=artifact["path"] or "",
                            has_tiktok_upload="Có",
                            upload_success="Có" if task["status"] == "completed" else "Không",
                            log=log,
                        )
                    )
        return rows


def export_pipeline_report(database: Database, destination: str | Path) -> Path:
    path = Path(destination)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    report_rows = build_pipeline_report_rows(database)
    data = [HEADERS, *[row.as_excel_row() for row in report_rows]]
    youtube_links = [None, *[row.youtube_channel_url for row in report_rows]]
    _write_xlsx(path, data, youtube_links)
    return path


def _row_without_rendered_artifact(connection, media, mapped_account) -> PipelineReportRow:
    return PipelineReportRow(
        youtube_channel=_youtube_channel_name(media),
        youtube_channel_url=_youtube_channel_url(media),
        tiktok_channel=mapped_account["name"] if mapped_account else "",
        video_link=media["source_url"] or f"https://www.youtube.com/watch?v={media['youtube_video_id']}",
        title=media["title"] or "",
        rendered_video_path="",
        has_tiktok_upload="Không",
        upload_success="",
        log=_latest_media_log(connection, media["id"]) or "Chưa có video đã cắt/render",
    )


def _row_without_upload_task(connection, media, mapped_account) -> PipelineReportRow:
    return PipelineReportRow(
        youtube_channel=_youtube_channel_name(media),
        youtube_channel_url=_youtube_channel_url(media),
        tiktok_channel=mapped_account["name"] if mapped_account else "",
        video_link=media["source_url"] or f"https://www.youtube.com/watch?v={media['youtube_video_id']}",
        title=media["title"] or "",
        rendered_video_path="",
        has_tiktok_upload="Không",
        upload_success="",
        log=_latest_media_log(connection, media["id"]) or "Đã cắt/render nhưng chưa tạo task upload TikTok",
    )


def _youtube_channel_name(media) -> str:
    return media["channel_title"] or media["channel_source_url"] or media["channel_id"] or ""


def _youtube_channel_url(media) -> str:
    if media["channel_source_url"]:
        return media["channel_source_url"]
    if media["channel_id"]:
        return f"https://www.youtube.com/channel/{media['channel_id']}"
    return ""


def _upload_task_log(connection, media_id: int, task_id: int, task_error: str | None) -> str:
    if task_error:
        return task_error
    job = connection.execute(
        "SELECT * FROM jobs WHERE idempotency_key=? ORDER BY id DESC LIMIT 1",
        (f"upload:{task_id}",),
    ).fetchone()
    if job and job["error_message"]:
        return job["error_message"]
    if job:
        event = connection.execute(
            "SELECT message FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job["id"],),
        ).fetchone()
        if event:
            return event["message"]
    return _latest_media_log(connection, media_id)


def _latest_media_log(connection, media_id: int) -> str:
    job = connection.execute(
        """SELECT * FROM jobs
           WHERE media_item_id=?
           ORDER BY updated_at DESC, id DESC LIMIT 1""",
        (media_id,),
    ).fetchone()
    if not job:
        return ""
    if job["error_message"]:
        return job["error_message"]
    event = connection.execute(
        "SELECT message FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    return event["message"] if event else ""


def _write_xlsx(path: Path, rows: Iterable[Iterable[str]], youtube_links: Iterable[str | None] | None = None) -> None:
    sheet, sheet_relationships = _worksheet_xml(rows, youtube_links)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", _content_types_xml())
        workbook.writestr("_rels/.rels", _root_rels_xml())
        workbook.writestr("xl/workbook.xml", _workbook_xml())
        workbook.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        workbook.writestr("xl/worksheets/sheet1.xml", sheet)
        if sheet_relationships:
            workbook.writestr("xl/worksheets/_rels/sheet1.xml.rels", sheet_relationships)


def _worksheet_xml(rows: Iterable[Iterable[str]], youtube_links: Iterable[str | None] | None = None) -> tuple[str, str]:
    xml_rows = []
    hyperlinks = []
    relationships = []
    links = list(youtube_links or [])
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value or ""))}</t></is></c>')
            if column_index == 1 and row_index > 1:
                link = links[row_index - 1] if row_index - 1 < len(links) else ""
                if link:
                    relationship_id = f"rIdHyper{len(relationships) + 1}"
                    hyperlinks.append(f'<hyperlink ref="{ref}" r:id="{relationship_id}"/>')
                    relationships.append(
                        f'<Relationship Id="{relationship_id}" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                        f'Target={quoteattr(link)} TargetMode="External"/>'
                    )
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    hyperlink_xml = f'<hyperlinks>{"".join(hyperlinks)}</hyperlinks>' if hyperlinks else ""
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft"/></sheetView></sheetViews>'
        '<sheetData>'
        f'{"".join(xml_rows)}'
        '</sheetData>'
        f'{hyperlink_xml}'
        '</worksheet>'
    )
    relationships_xml = ""
    if relationships:
        relationships_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(relationships)}'
            '</Relationships>'
        )
    return worksheet, relationships_xml


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )


def _workbook_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="MMO Report" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )


def _workbook_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
