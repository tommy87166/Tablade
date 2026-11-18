//! Tablade 的 Rust 模組：解 zip 裡 Deflate64 壓縮的檔（docs/zip-lines-plan.md、native/README.md）。
//!
//! 只做「把壓縮過的位元組解開」這一步；zip 的目錄、位置、CRC、切段、轉碼都在 Python
//! （src/tablade/ingest/zip_lines.py）。不讀寫檔、不連網路、不呼叫外部程式。
#![forbid(unsafe_code)]

use deflate64::InflaterManaged;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// 用到的 deflate64 crate 版本（tests/test_native.py 核對 Cargo.lock）。
const DEFLATE64_VERSION: &str = "0.1.12";

/// 一個 Deflate64 的串流：一段一段餵壓縮過的位元組，每次回傳這次解得出來的位元組。
#[pyclass(module = "tablade_native")]
struct Inflater {
    inner: Box<InflaterManaged>,
    buf: Vec<u8>,
}

/// 解開 data（接在之前餵過的後面）；壞掉的資料回傳 Err。
fn inflate_into(inner: &mut InflaterManaged, buf: &mut [u8], data: &[u8]) -> Result<Vec<u8>, ()> {
    let mut out = Vec::with_capacity(data.len().saturating_mul(3));
    let mut input = data;
    loop {
        let r = inner.inflate(input, buf);
        out.extend_from_slice(&buf[..r.bytes_written]);
        input = &input[r.bytes_consumed..];
        if r.data_error {
            return Err(());
        }
        if inner.finished() || (r.bytes_written == 0 && input.is_empty()) {
            return Ok(out);
        }
    }
}

#[pymethods]
impl Inflater {
    #[new]
    fn new() -> Self {
        Self { inner: Box::new(InflaterManaged::new()), buf: vec![0u8; 1 << 16] }
    }

    /// 餵一段壓縮過的位元組，回傳這次解得出來的；解壓時放開 Python 的全域鎖。
    fn inflate<'py>(&mut self, py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
        let inner = &mut self.inner;
        let buf = &mut self.buf;
        match py.detach(|| inflate_into(inner, buf, data)) {
            Ok(out) => Ok(PyBytes::new(py, &out)),
            Err(()) => Err(PyValueError::new_err("invalid deflate64 data")),
        }
    }

    /// 已經讀到串流的結尾。
    #[getter]
    fn finished(&self) -> bool {
        self.inner.finished()
    }
}

#[pymodule]
fn tablade_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("DEFLATE64_VERSION", DEFLATE64_VERSION)?;
    m.add_class::<Inflater>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// zlib 用 Z_HUFFMAN_ONLY 壓的 "ab\r\nab\r\n"（只有字面值、沒有重複比對，所以也是合法的 Deflate64）。
    const HUFFMAN_ONLY: &[u8] = &[
        0x4b, 0x4c, 0xe2, 0xe5, 0x4a, 0x4c, 0xe2, 0xe5, 0x02, 0x00,
    ];

    #[test]
    fn decodes_huffman_only_data_in_any_pieces() {
        for piece in [1usize, 3, HUFFMAN_ONLY.len()] {
            let mut inner = InflaterManaged::new();
            let mut buf = vec![0u8; 16];
            let mut out = Vec::new();
            for chunk in HUFFMAN_ONLY.chunks(piece) {
                out.extend(inflate_into(&mut inner, &mut buf, chunk).unwrap());
            }
            assert_eq!(out, b"ab\r\nab\r\n");
            assert!(inner.finished());
        }
    }

    #[test]
    fn rejects_broken_data() {
        let mut inner = InflaterManaged::new();
        let mut buf = vec![0u8; 16];
        assert!(inflate_into(&mut inner, &mut buf, &[0xff, 0xff, 0xff, 0xff]).is_err());
    }
}
