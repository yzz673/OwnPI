use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

/// Codex Ctrl/Alt+V parity: read a bitmap from the system clipboard, persist it
/// as a PNG next to other attached images, and return its path for attachment.
/// Returns Ok(None) when the clipboard holds no image or the platform has no
/// clipboard session (headless SSH) — callers fall back to plain text input.
pub(crate) fn paste_image_from_clipboard() -> Result<Option<PathBuf>, String> {
    let Some(image) = read_clipboard_image()? else {
        return Ok(None);
    };
    let dir = std::env::temp_dir().join("neuro-agent-images");
    std::fs::create_dir_all(&dir)
        .map_err(|error| format!("failed to create image dir {dir:?}: {error}"))?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);
    let path = dir.join(format!("clipboard-{stamp}.png"));
    write_png(&path, &image.bytes, image.width, image.height)?;
    Ok(Some(path))
}

struct ClipboardImage {
    bytes: Vec<u8>,
    width: u32,
    height: u32,
}

fn read_clipboard_image() -> Result<Option<ClipboardImage>, String> {
    let mut clipboard =
        arboard::Clipboard::new().map_err(|error| format!("clipboard unavailable: {error}"))?;
    let image = match clipboard.get_image() {
        Ok(image) => image,
        Err(arboard::Error::ContentNotAvailable) => return Ok(None),
        Err(error) => return Err(format!("failed to read clipboard image: {error}")),
    };
    Ok(Some(ClipboardImage {
        bytes: image.bytes.into_owned(),
        width: image.width as u32,
        height: image.height as u32,
    }))
}

fn write_png(path: &PathBuf, bytes: &[u8], width: u32, height: u32) -> Result<(), String> {
    let file = std::fs::File::create(path)
        .map_err(|error| format!("failed to create {path:?}: {error}"))?;
    let mut encoder = png::Encoder::new(file, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder
        .write_header()
        .map_err(|error| format!("failed to write PNG header: {error}"))?;
    writer
        .write_image_data(bytes)
        .map_err(|error| format!("failed to write PNG data: {error}"))?;
    writer
        .finish()
        .map_err(|error| format!("failed to finalize PNG: {error}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn write_png_produces_valid_png_header() {
        let path = std::env::temp_dir().join("neuro-test-clipboard.png");
        let pixels = vec![200u8, 30, 30, 255, 0, 0, 0, 255];
        write_png(&path, &pixels, 2, 1).expect("write png");

        let written = std::fs::read(&path).expect("read png");
        assert_eq!(&written[..8], b"\x89PNG\r\n\x1a\n");
        let mut file = std::fs::File::open(&path).expect("open");
        let mut signature = [0u8; 8];
        use std::io::Read;
        file.read_exact(&mut signature).expect("signature");
        assert_eq!(signature, *b"\x89PNG\r\n\x1a\n");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn png_roundtrip_preserves_dimensions() {
        let path = std::env::temp_dir().join("neuro-test-clipboard-dims.png");
        let pixels = vec![0u8; 4 * 4 * 4];
        write_png(&path, &pixels, 4, 4).expect("write");

        let decoder = png::Decoder::new(std::fs::File::open(&path).expect("open"));
        let reader = decoder.read_info().expect("info");
        let info = reader.info();
        assert_eq!(info.width, 4);
        assert_eq!(info.height, 4);
        let _ = std::fs::remove_file(&path);
    }
}
