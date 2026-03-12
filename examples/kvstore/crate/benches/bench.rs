use criterion::{criterion_group, criterion_main, Criterion};
use kvstore::KvStore;
use tempfile::NamedTempFile;

fn bench_writes(c: &mut Criterion) {
    c.bench_function("set_1k", |b| {
        b.iter(|| {
            let f = NamedTempFile::new().unwrap();
            let mut db = KvStore::open(f.path()).unwrap();
            for i in 0..1_000u32 {
                db.set(format!("key{i}"), format!("value{i}")).unwrap();
            }
        });
    });
}

fn bench_reads(c: &mut Criterion) {
    let f = NamedTempFile::new().unwrap();
    let mut db = KvStore::open(f.path()).unwrap();
    for i in 0..1_000u32 {
        db.set(format!("key{i}"), format!("value{i}")).unwrap();
    }
    c.bench_function("get_1k", |b| {
        b.iter(|| {
            for i in 0..1_000u32 {
                criterion::black_box(db.get(&format!("key{i}")));
            }
        });
    });
}

fn bench_mixed(c: &mut Criterion) {
    c.bench_function("mixed_rw_1k", |b| {
        b.iter(|| {
            let f = NamedTempFile::new().unwrap();
            let mut db = KvStore::open(f.path()).unwrap();
            for i in 0..500u32 {
                db.set(format!("key{i}"), format!("value{i}")).unwrap();
            }
            for i in 0..500u32 {
                criterion::black_box(db.get(&format!("key{i}")));
            }
        });
    });
}

criterion_group!(benches, bench_writes, bench_reads, bench_mixed);
criterion_main!(benches);
