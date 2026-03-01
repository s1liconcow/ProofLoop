//! Starter crate for tinyxml2 behavior porting.

/// Decode a minimal subset of XML entities.
/// This is intentionally incomplete as a baseline for agent optimization/porting.
pub fn decode_entities(input: &str) -> String {
    input
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
}

#[cfg(test)]
mod tests {
    use super::decode_entities;

    #[test]
    fn decodes_basic_entities() {
        assert_eq!(decode_entities("A &amp; B"), "A & B");
        assert_eq!(decode_entities("&lt;tag&gt;"), "<tag>");
    }
}
