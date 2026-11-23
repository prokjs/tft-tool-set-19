use serde_json::{json, Value};
use tft_core::{model::GameData, pool::pool};

fn data() -> GameData {
    let rows = [("A", vec!["Tank"]), ("B", vec!["Mage"]), ("Both", vec!["Tank", "Mage"]), ("OtherTank", vec!["Tank"]), ("C", vec!["Slayer"]), ("D", vec!["Support"]), ("E", vec!["Jazz"]), ("F", vec!["Rock"]), ("Sona", vec!["Producer", "Collab"])];
    let champions: Vec<Value> = rows.into_iter().map(|(name, traits)| json!({"name":name,"cost":1,"traits":traits})).collect();
    let traits: Vec<Value> = ["Tank","Mage","Slayer","Support","Jazz","Rock","Producer","Collab"].iter().map(|name| json!({"name":name,"breakpoints":[1],"unique":false})).collect();
    GameData::from_value(json!({"champions":champions,"traits":traits})).unwrap()
}
fn has(value: &Value, field: &str, name: &str) -> bool { value[field].as_array().unwrap().iter().any(|item| item == name) }

#[test]
fn incremental_filters_restore_only_disjoint_candidates() {
    let data = data();
    let one = pool(&data, &json!({"selected":["A"]})).unwrap();
    for name in ["A", "OtherTank", "Both", "Sona"] { assert!(!has(&one,"eligible",name)); }
    assert!(has(&one,"visible","A"));
    assert!(has(&one,"visible","Sona"));
    assert!(!has(&one,"visible","Both"));
    let removed = pool(&data, &json!({"selected":["B"]})).unwrap();
    assert!(has(&removed,"eligible","OtherTank"));
    assert!(!has(&removed,"eligible","Both"));
    assert!(has(&pool(&data,&json!({})).unwrap(),"eligible","Both"));
}

#[test]
fn fifth_sixth_sona_and_locked_full_roster_contract() {
    let data = data();
    let five = pool(&data, &json!({"selected":["A","B","C","D","E","Sona"]})).unwrap();
    assert!(has(&five,"eligible","F"));
    let six = pool(&data, &json!({"selected":["A","B","C","D","E","F"]})).unwrap();
    assert_eq!(six["eligible"],json!([]));
    assert_eq!(six["visible"],json!(["A","B","C","D","E","F","Sona"]));
    let locked = pool(&data, &json!({"selected":["A","B","C","D","E","F"],"locked":true})).unwrap();
    assert_eq!(locked["visible"].as_array().unwrap().len(),data.champions.len());
}

#[test]
fn exclusive_forms_stars_and_invalid_inputs() {
    let mut data = data();
    data.champions.get_mut("A").unwrap().exclusive_group = Some("forms".into());
    data.champions.get_mut("F").unwrap().exclusive_group = Some("forms".into());
    data.champions.get_mut("A").unwrap().cost = 4;
    data.champions.get_mut("A").unwrap().role = "AP Tank".into();
    let result = pool(&data,&json!({"selected":["A"]})).unwrap();
    assert!(!has(&result,"eligible","F"));
    assert_eq!(result["stars"]["A"],1);
    assert_eq!(pool(&data,&json!({"size":8,"stars":{"A":3}})).unwrap()["stars"]["A"],3);
    for payload in [json!({"selected":["Missing"]}),json!({"selected":["A","A"]}),json!({"locked":1}),json!({"size":true}),json!({"stars":{"A":true}}),json!({"stars":{"A":5}}),json!({"stars":{"Missing":1}})] { assert!(pool(&data,&payload).is_err(),"{payload}"); }
}

#[test]
fn picker_never_borrows_guide_progression_or_featured_band_ranks() {
    let data = tft_core::resources::bundled_data();
    for level in [4, 5, 6, 7, 8, 10, 11] {
        for band in ["", "K/DA"] {
            let result = pool(data, &json!({"size":level,"sona_band":band})).unwrap();
            for name in data.champions.keys() { assert_eq!(result["stars"][name],1,"{name}, level {level}, band {band}"); }
            let explicit = pool(data, &json!({"size":level,"sona_band":band,"stars":{"Lillia":3,"Rell":2,"Sona":4}})).unwrap();
            assert_eq!(explicit["stars"]["Lillia"],3);
            assert_eq!(explicit["stars"]["Rell"],2);
            assert_eq!(explicit["stars"]["Sona"],4);
            assert_eq!(explicit["stars"]["Neeko"],1);
        }
    }
}
