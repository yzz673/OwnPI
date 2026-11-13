from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.vision import ImagePreparationError, load_local_image_for_prompt
from ownpi.tools.common import metadata_for_context, resolve_file_tool_path

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


async def run_view_image(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    detail = str(arguments.get('detail', 'high') or 'high').strip().lower()
    if detail not in {'high', 'original'}:
        return ToolResult(ok=False, tool_name='view_image', error=f"view_image.detail only supports 'high' or 'original', got {detail!r}")
    try:
        path = resolve_file_tool_path(
            context, arguments.get('path'), tool_name='view_image', access='read',
        )
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, tool_name='view_image', error=str(exc))
    if not path.exists() or not path.is_file():
        return ToolResult(ok=False, tool_name='view_image', error=f'image file not found: {path}')
    try:
        image = load_local_image_for_prompt(path, detail=detail)
    except ImagePreparationError as exc:
        return ToolResult(ok=False, tool_name='view_image', error=f'failed to prepare image: {exc}')
    data = {
        'path': str(path),
        'mime_type': image.mime_type,
        'width': image.width,
        'height': image.height,
        'detail': image.detail,
        'image_url_preview': f'<image data URL omitted: {len(image.image_url)} chars>',
    }
    return ToolResult(
        ok=True,
        tool_name='view_image',
        data=data,
        content_items=(ModelContentItem.input_image(image_url=image.image_url, detail=image.detail, source_path=str(path), mime_type=image.mime_type),),
        stdout=f"path={path}\nmime_type={image.mime_type} size={image.width}x{image.height} detail={image.detail}\nimage_url=<omitted {len(image.image_url)} chars>",
        metadata=metadata_for_context(context, path=str(path), mime_type=image.mime_type),
    )
