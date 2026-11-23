use crate::model::GameData;
use serde_json::Value;
use std::sync::LazyLock;

static DATA: LazyLock<GameData> = LazyLock::new(|| {
    GameData::from_value(serde_json::from_str(include_str!("../../../data/set19.json"))
        .expect("bundled Set 19 JSON is valid")).expect("bundled Set 19 schema is valid")
});
static METADATA: LazyLock<Value> = LazyLock::new(|| {
    let mut metadata:Value=serde_json::from_str(include_str!("../../../data/app_metadata.json")).expect("bundled application metadata is valid");
    metadata["artwork"]=serde_json::from_str(include_str!("../../../assets/manifest.json")).expect("bundled artwork manifest is valid");
    metadata
});
static ITEMS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/item_builds.json")).expect("bundled item plans are valid")
});
static TEAM_MAPPING: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/team_planner_ids.json")).expect("bundled team mapping is valid")
});

pub fn bundled_data() -> &'static GameData { &DATA }
pub fn metadata() -> &'static Value { &METADATA }
pub fn item_builds() -> &'static Value { &ITEMS }
pub fn team_mapping() -> &'static Value { &TEAM_MAPPING }
