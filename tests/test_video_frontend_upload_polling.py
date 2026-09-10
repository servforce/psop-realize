"""Frontend wiring checks; behavioral races are covered by the runtime harness."""
from pathlib import Path
import re

ROOT = Path('static')


def test_entry_uses_local_alpine_and_compiled_css():
    html = (ROOT / 'index.html').read_text()
    assert 'assets/css/style.compiled.css' in html
    assert 'assets/js/init-alpine.js' in html
    assert '<base href="/">' in html
    assert not re.search(r'(?:src|href)=["\']https?://', html)
    init = (ROOT / 'assets/js/init-alpine.js').read_text()
    assert '/node_modules/alpinejs/dist/module.esm.js' in init
    assert "Alpine.data('videosPage'" in init
    assert "Alpine.data('uploadsPage'" in init
    assert "Alpine.data('uploadDrawer'" in init
    assert "Alpine.data('usagePage'" in init


def test_fragments_have_no_document_or_script_tags():
    for page in (ROOT / 'pages').glob('*.html'):
        text = page.read_text()
        assert 'x-data=' in text
        assert not re.search(r'<(?:!doctype|html|head|body|script)\b', text, re.I), page


def test_frame_initializes_injected_tree_and_cleans_previous_pages():
    source = (ROOT / 'assets/js/components/frame.js').read_text()
    assert 'container.innerHTML = html;' in source
    assert 'window.Alpine.initTree(container);' in source
    assert 'window.Alpine.destroyTree(child)' in source
    assert 'controller?.abort()' in source


def test_font_assets_are_local_and_valid_woff2():
    for style in ('rounded', 'outlined', 'sharp'):
        assert (ROOT / f'assets/fonts/material-symbols-{style}.woff2').read_bytes().startswith(b'wOF2')


def test_upload_entry_uses_a_global_accessible_drawer_not_a_page_navigation():
    shell = (ROOT / 'index.html').read_text()
    videos = (ROOT / 'pages/videos.html').read_text()
    assert 'id="upload-drawer" x-data="uploadDrawer"' in shell
    assert 'aria-labelledby="upload-drawer-title"' in shell
    assert '@cancel.prevent="close()"' in shell
    assert 'aria-label="关闭上传抽屉"' in shell
    assert "go('/uploads')" not in videos
    assert videos.count("$dispatch('psop:upload-open')") == 2
    assert re.search(r'<h2\b[^>]*>文件解析</h2>', videos)
    assert '视频目录' not in videos
    # The drawer must never dispose the global upload controller on close.
    drawer = (ROOT / 'assets/js/components/uploadDrawer.js').read_text()
    assert 'startUpload' not in drawer
    assert 'uploader' not in drawer


def test_file_list_has_single_line_columns_and_separate_delete_actions():
    videos = (ROOT / 'pages/videos.html').read_text()
    shell = (ROOT / 'index.html').read_text()
    assert 'class="data-table video-table"' in videos
    assert '<th scope="col">文件名称</th>' in videos
    assert 'delete-file-button' in videos
    assert 'aria-labelledby="delete-video-title"' in videos
    table = videos.split('<table', 1)[1].split('</table>', 1)[0]
    assert 'chevron_right' not in table
    assert 'aria-label="文件解析分页"' in videos
    assert '<footer' not in videos
    assert 'PSOP · 操作知识工作台' not in shell
    assert '>视频工作台</p>' not in shell
    assert 'PSOP Realize 首页' in shell
