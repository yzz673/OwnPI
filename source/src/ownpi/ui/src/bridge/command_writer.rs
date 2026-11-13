//! A bounded FIFO owns the pipe. Rendering never waits for write/flush/log I/O.
use anyhow::{anyhow, Result};
use serde_json::Value;
use std::io::Write;
use std::sync::mpsc::{self, Receiver, SyncSender};

pub(crate) struct CommandWriter {
    sender: SyncSender<Value>,
    errors: Receiver<String>,
}

impl CommandWriter {
    pub(crate) fn spawn(mut pipe: impl Write + Send + 'static) -> Self {
        let (sender, commands) = mpsc::sync_channel::<Value>(64);
        let (errors_tx, errors) = mpsc::channel();
        std::thread::spawn(move || {
            for payload in commands {
                let result = (|| -> Result<()> {
                    super::append_debug_log("ui_command_sent", &payload);
                    writeln!(pipe, "{}", serde_json::to_string(&payload)?)?;
                    pipe.flush()?;
                    Ok(())
                })();
                if let Err(error) = result {
                    let _ = errors_tx.send(format!(
                        "Bridge command delivery failed: {error}. No decision was retried."
                    ));
                    break;
                }
            }
        });
        Self { sender, errors }
    }

    pub(crate) fn send(&self, payload: Value) -> Result<()> {
        self.sender.try_send(payload).map_err(|error| {
            anyhow!("Bridge command queue unavailable: {error}. The decision was not queued.")
        })
    }

    pub(crate) fn take_error(&self) -> Option<String> {
        self.errors.try_recv().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    struct SlowPipe {
        entered: mpsc::Sender<()>,
        release: Receiver<()>,
    }
    impl Write for SlowPipe {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            let _ = self.entered.send(());
            let _ = self.release.recv();
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn blocked_pipe_never_blocks_ui_and_queue_is_bounded() {
        let (entered, ready) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let writer = CommandWriter::spawn(SlowPipe {
            entered,
            release: wait,
        });
        writer.send(Value::Null).unwrap();
        ready.recv_timeout(Duration::from_secs(1)).unwrap();
        let started = Instant::now();
        for _ in 0..64 {
            writer.send(Value::Null).unwrap();
        }
        assert!(writer.send(Value::Null).is_err());
        assert!(started.elapsed() < Duration::from_millis(33));
        drop(release);
    }

    #[test]
    fn preserves_fifo() {
        struct Pipe(Arc<Mutex<Vec<u8>>>);
        impl Write for Pipe {
            fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
                self.0.lock().unwrap().extend_from_slice(bytes);
                Ok(bytes.len())
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let bytes = Arc::new(Mutex::new(Vec::new()));
        let writer = CommandWriter::spawn(Pipe(bytes.clone()));
        for i in 0..5 {
            writer.send(serde_json::json!({"id":i})).unwrap();
        }
        let started = Instant::now();
        loop {
            let actual = bytes.lock().unwrap().clone();
            if actual.iter().filter(|&&b| b == b'\n').count() == 5 {
                let ids: Vec<_> = String::from_utf8(actual)
                    .unwrap()
                    .lines()
                    .map(|line| {
                        serde_json::from_str::<Value>(line).unwrap()["id"]
                            .as_u64()
                            .unwrap()
                    })
                    .collect();
                assert_eq!(ids, vec![0, 1, 2, 3, 4]);
                break;
            }
            assert!(started.elapsed() < Duration::from_secs(1));
            std::thread::yield_now();
        }
    }
}
