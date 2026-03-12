use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

/// A simple persistent key-value store backed by an append-only log.
/// Writes are appended to disk; an in-memory HashMap serves reads.
pub struct KvStore {
    index: HashMap<String, String>,
    log: File,
}

impl KvStore {
    pub fn open(path: impl AsRef<Path>) -> std::io::Result<Self> {
        let path = path.as_ref();
        let mut index = HashMap::new();

        if path.exists() {
            let reader = BufReader::new(File::open(path)?);
            for line in reader.lines() {
                let line = line?;
                match line.split_once('\t') {
                    Some((k, "\x00")) => { index.remove(k); }
                    Some((k, v))      => { index.insert(k.to_owned(), v.to_owned()); }
                    None              => {}
                }
            }
        }

        let log = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self { index, log })
    }

    pub fn set(&mut self, key: String, value: String) -> std::io::Result<()> {
        writeln!(self.log, "{}\t{}", key, value)?;
        self.index.insert(key, value);
        Ok(())
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.index.get(key).map(String::as_str)
    }

    pub fn remove(&mut self, key: &str) -> std::io::Result<()> {
        writeln!(self.log, "{}\t\x00", key)?;
        self.index.remove(key);
        Ok(())
    }

    pub fn len(&self) -> usize {
        self.index.len()
    }

    pub fn is_empty(&self) -> bool {
        self.index.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    #[test]
    fn set_and_get() {
        let f = NamedTempFile::new().unwrap();
        let mut db = KvStore::open(f.path()).unwrap();
        db.set("hello".into(), "world".into()).unwrap();
        assert_eq!(db.get("hello"), Some("world"));
    }

    #[test]
    fn remove() {
        let f = NamedTempFile::new().unwrap();
        let mut db = KvStore::open(f.path()).unwrap();
        db.set("k".into(), "v".into()).unwrap();
        db.remove("k").unwrap();
        assert_eq!(db.get("k"), None);
    }

    #[test]
    fn persistence_across_reopen() {
        let f = NamedTempFile::new().unwrap();
        {
            let mut db = KvStore::open(f.path()).unwrap();
            db.set("a".into(), "1".into()).unwrap();
            db.set("b".into(), "2".into()).unwrap();
            db.remove("a").unwrap();
        }
        let db = KvStore::open(f.path()).unwrap();
        assert_eq!(db.get("a"), None);
        assert_eq!(db.get("b"), Some("2"));
    }

    #[test]
    fn overwrite() {
        let f = NamedTempFile::new().unwrap();
        let mut db = KvStore::open(f.path()).unwrap();
        db.set("x".into(), "old".into()).unwrap();
        db.set("x".into(), "new".into()).unwrap();
        assert_eq!(db.get("x"), Some("new"));
    }
}
