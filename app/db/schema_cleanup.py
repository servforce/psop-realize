"""Idempotent removal of retired schema, isolated from active application models."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def remove_retired_media_schema(connection: Connection) -> None:
    connection.execute(text("DROP TABLE IF EXISTS wireframe_jobs"))
    columns = {column["name"] for column in inspect(connection).get_columns("video_frames")}
    if "selected_for_wireframe" in columns:
        connection.execute(text("ALTER TABLE video_frames DROP COLUMN selected_for_wireframe"))
