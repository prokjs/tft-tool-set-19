use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use tft_core::{cap_out, guidance, model::{GameData, OptimizeOptions}, optimizer, resources};

fn fixture() -> GameData {
    GameData::from_value(json!({"traits":[{"name":"Collab","breakpoints":[3],"unique":false}],"champions":[
        {"name":"Ahri","cost":4,"traits":[],"role":"Magic Caster"},
        {"name":"Alune","cost":4,"traits":[],"role":"Magic Caster"},
        {"name":"Leona","cost":4,"traits":[],"role":"Magic Tank"},
        {"name":"Front","cost":1,"traits":[],"role":"Attack Tank"},
        {"name":"Filler A","cost":1,"traits":[],"role":"Attack Marksman"},
        {"name":"Filler B","cost":1,"traits":[],"role":"Attack Fighter"},
        {"name":"Fighter A","cost":4,"traits":[],"role":"Attack Fighter"},
        {"name":"Fighter B","cost":4,"traits":[],"role":"Attack Fighter"},
        {"name":"Bard","cost":1,"traits":[],"role":"Magic Caster"}
    ]})).unwrap()
}
fn starting() -> Value { json!({"champions":["Ahri","Leona","Front","Filler A","Filler B"],"stars":{"Ahri":2,"Leona":2}}) }
fn source(data:&GameData, locked:Value) -> Value {
    let context=json!({"size":5,"mode":"balanced","min_collab":0,"collab":[],"locked":locked,"excluded":[],"stars":{"Ahri":2,"Leona":2},"sona_band":""});
    let options=OptimizeOptions::from_value(&context).unwrap();
    let names=starting()["champions"].as_array().unwrap().iter().map(|value|value.as_str().unwrap().to_owned()).collect::<Vec<_>>();
    let mut board=optimizer::evaluate_team(data,&names,&options.collab,&options.mode,&options.sona_band,&options.stars,Some(5)).unwrap();
    board["level"]=json!(5);
    guidance::annotate_result(&mut board,data).unwrap();
    board["planning_context"]=context;
    board
}
fn mage_inventory(holder:Value) -> Value {
    json!([
        {"id":"mage-shojin","item":"Spear of Shojin","holder":holder},
        {"id":"mage-jg","item":"Jeweled Gauntlet","holder":holder},
        {"id":"mage-cap","item":"Rabadon's Deathcap","holder":holder}
    ])
}
fn call(data:&GameData, source:&Value, inventory:Value) -> Result<Value,String> {
    cap_out::recommend(data,&json!({"result":source,"starting_board":starting(),"item_inventory":inventory}))
}
fn ledger(board:&Value) -> BTreeMap<String,String> {
    let catalog:Value=serde_json::from_str(include_str!("../../../data/item_catalog.json")).unwrap();
    let mut ledger=BTreeMap::new();
    let mut capacity:BTreeMap<String,u64>=BTreeMap::new();
    let mut uniques=BTreeSet::new();
    for build in board["planned_item_builds"].as_array().unwrap() {
        let holder=build["holder"].as_str().unwrap();
        assert!(board["champions"].as_array().unwrap().iter().any(|name|name.as_str()==Some(holder)));
        let ids=build["item_ids"].as_array().unwrap();
        let names=build["items"].as_array().unwrap();
        assert_eq!(ids.len(),names.len());
        for (id,name) in ids.iter().zip(names) {
            let name=name.as_str().unwrap();
            assert!(ledger.insert(id.as_str().unwrap().to_owned(),name.to_owned()).is_none(),"reused physical item");
            let entry=&catalog["items"][name];
            assert_eq!(entry["equippable"],true);
            *capacity.entry(holder.to_owned()).or_default()+=entry["slot_cost"].as_u64().unwrap();
            if entry["unique_per_champion"]==true {assert!(uniques.insert((holder.to_owned(),name.to_owned())));}
        }
    }
    assert!(capacity.values().all(|&slots|slots<=3));
    for item in board["item_bench"].as_array().unwrap() {
        assert!(item["holder"].is_null());
        assert!(ledger.insert(item["id"].as_str().unwrap().to_owned(),item["item"].as_str().unwrap().to_owned()).is_none(),"assigned and benched twice");
    }
    ledger
}
fn conserved(output:&Value) {
    let budget:BTreeMap<_,_>=output["item_budget"].as_array().unwrap().iter()
        .map(|item|(item["id"].as_str().unwrap().to_owned(),item["item"].as_str().unwrap().to_owned())).collect();
    assert_eq!(budget.len(),output["item_budget"].as_array().unwrap().len());
    assert_eq!(ledger(&output["from_board"]),budget);
    assert_eq!(ledger(&output["board"]),budget);
    for phase in output["phases"].as_array().unwrap() {assert_eq!(ledger(&phase["board"]),budget);}
    assert_eq!(output["board"]["item_fit"]["score"],output["board"]["item_fit"]["potential_score"]);
}
fn holder(board:&Value,id:&str) -> Option<String> {
    board["planned_item_builds"].as_array().unwrap().iter().find(|build|
        build["item_ids"].as_array().unwrap().iter().any(|value|value.as_str()==Some(id)))
        .and_then(|build|build["holder"].as_str()).map(str::to_owned)
}

#[test]
fn mage_instances_survive_every_sale_phase_without_becoming_fighter_trios() {
    let data=fixture();
    let input=source(&data,json!(["Alune","Leona","Front","Filler A","Filler B"]));
    let original=input.clone();
    let out=call(&data,&input,mage_inventory(json!("Ahri"))).unwrap();
    conserved(&out);
    assert_eq!(input,original);
    assert_eq!(out["item_mode"],"collected");
    assert_eq!(out["item_budget"].as_array().unwrap().len(),3);
    assert_eq!(holder(&out["from_board"],"mage-shojin").as_deref(),Some("Ahri"));
    assert_eq!(holder(&out["board"],"mage-shojin").as_deref(),Some("Alune"));
    let moves:Vec<_>=out["phases"].as_array().unwrap().iter().flat_map(|phase|phase["item_transfers"].as_array().unwrap()).collect();
    assert!(moves.iter().any(|transfer|transfer["method"]=="sell_outgoing" && transfer["outgoing"]=="Ahri"));
    for item in ledger(&out["board"]).values() {assert!(!["Bloodthirster","Sterak's Gage","Titan's Resolve"].contains(&item.as_str()));}
}

#[test]
fn retained_source_custody_is_not_a_free_remover() {
    let data=fixture();
    let input=source(&data,json!(["Ahri","Alune","Leona","Front","Filler A"]));
    let out=call(&data,&input,mage_inventory(json!("Ahri"))).unwrap();
    conserved(&out);
    for board in std::iter::once(&out["from_board"]).chain(out["phases"].as_array().unwrap().iter().map(|phase|&phase["board"])).chain(std::iter::once(&out["board"])) {
        assert_eq!(holder(board,"mage-shojin").as_deref(),Some("Ahri"));
    }
    assert!(out["item_transfers"].as_array().unwrap().iter().all(|transfer|transfer["method"]!="remover_or_later_sale"));
}

#[test]
fn explicit_zero_never_regenerates_source_recommendations() {
    let data=fixture();
    let input=source(&data,starting()["champions"].clone());
    assert!(!input["planned_item_builds"].as_array().unwrap().is_empty());
    let out=call(&data,&input,json!([])).unwrap();
    conserved(&out);
    assert_eq!(out["item_mode"],"collected");
    assert!(out["item_budget"].as_array().unwrap().is_empty());
    assert!(out["board"]["planned_item_builds"].as_array().unwrap().is_empty());
}

#[test]
fn arbitrary_inventory_counts_and_duplicate_names_remain_distinct() {
    let data=fixture();
    let input=source(&data,starting()["champions"].clone());
    let inventory:Vec<_>=(0..29).map(|index|json!({"id":format!("copy-{index}"),"item":if index%2==0 {"Rabadon's Deathcap"} else {"Warmog's Armor"},"holder":null})).collect();
    let out=call(&data,&input,json!(inventory)).unwrap();
    conserved(&out);
    assert_eq!(out["item_budget"].as_array().unwrap().len(),29);
    assert!(out["board"]["item_bench"].as_array().unwrap().len()>=14);
    assert_eq!(ledger(&out["board"]).values().filter(|name|name.as_str()=="Rabadon's Deathcap").count(),15);
}

#[test]
fn component_custody_uses_a_slot_without_crafting_or_consuming() {
    let data=fixture();
    let input=source(&data,starting()["champions"].clone());
    let inventory=json!([
        {"id":"held-tear","item":"Tear Of The Goddess","holder":"Ahri"},
        {"id":"bench-sword","item":"B.F. Sword","holder":null},
        {"id":"remover","item":"Item Remover","holder":null},
        {"id":"cap","item":"Rabadon's Deathcap","holder":null}
    ]);
    let out=call(&data,&input,inventory).unwrap();
    conserved(&out);
    assert_eq!(holder(&out["board"],"held-tear").as_deref(),Some("Ahri"));
    assert_eq!(holder(&out["board"],"bench-sword"),None);
    assert_eq!(holder(&out["board"],"remover"),None);
    assert_eq!(ledger(&out["board"]).len(),4);
}

#[test]
fn all_slot_gloves_and_unique_artifacts_respect_source_restrictions() {
    let data=fixture();
    let input=source(&data,starting()["champions"].clone());
    let out=call(&data,&input,json!([
        {"id":"gloves","item":"Thief's Gloves","holder":"Ahri"},
        {"id":"cap","item":"Rabadon's Deathcap","holder":null},
        {"id":"artifact-a","item":"Death's Defiance","holder":null},
        {"id":"artifact-b","item":"Death's Defiance","holder":null}
    ])).unwrap();
    conserved(&out);
    assert_eq!(holder(&out["board"],"gloves").as_deref(),Some("Ahri"));
    assert_eq!(holder(&out["board"],"cap"),None);
    assert_eq!(out["board"]["item_fit"]["unmodelled_special_effects"],true);
    assert!(call(&data,&input,json!([
        {"id":"unique-1","item":"Death's Defiance","holder":"Ahri"},
        {"id":"unique-2","item":"Death's Defiance","holder":"Ahri"}
    ])).is_err());
}

#[test]
fn malformed_or_contradictory_inventory_is_rejected_not_dropped() {
    let data=fixture();
    let input=source(&data,starting()["champions"].clone());
    let malformed=vec![
        json!({"count":3}),
        json!([{"id":"","item":"Spear of Shojin","holder":null}]),
        json!([{"id":"same","item":"Spear of Shojin","holder":null},{"id":"same","item":"Jeweled Gauntlet","holder":null}]),
        json!([{"id":"unknown","item":"Invented Item","holder":null}]),
        json!([{"id":"absent","item":"Spear of Shojin","holder":"Alune"}]),
        json!([{"id":"holder","item":"Spear of Shojin","holder":7}]),
        json!([{"id":"count","item":"Spear of Shojin","holder":null,"count":3}]),
        json!([{"id":"consumed","item":"Item Remover","holder":"Ahri"}]),
        json!([{"id":"restricted","item":"Bard's Horn of the Traveler","holder":"Ahri"}]),
        json!([{"id":"g","item":"Thief's Gloves","holder":"Ahri"},{"id":"s","item":"Spear of Shojin","holder":"Ahri"}]),
        json!([{"id":"a","item":"Spear of Shojin","holder":"Ahri"},{"id":"b","item":"Jeweled Gauntlet","holder":"Ahri"},{"id":"c","item":"Rabadon's Deathcap","holder":"Ahri"},{"id":"d","item":"Tear Of The Goddess","holder":"Ahri"}]),
    ];
    for inventory in malformed {assert!(call(&data,&input,inventory.clone()).is_err(),"accepted malformed inventory: {inventory}");}
}

#[test]
fn planned_flex_alternatives_create_one_legal_loadout_not_copies() {
    let data=fixture();
    let mut input=source(&data,starting()["champions"].clone());
    input["planned_item_builds"]=json!([
        {"kind":"ap","holder":"Ahri","target":"Ahri","items":["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"],"source":"original mage plan"},
        {"kind":"ad","holder":"Ahri","target":"Ahri","items":["Bloodthirster","Titan's Resolve","Sterak's Gage"],"source":"alternative only"},
        {"kind":"flex","holder":"Ahri","target":"Ahri","items":["Guinsoo's Rageblade","Infinity Edge","Kraken's Fury"],"source":"alternative only"}
    ]);
    let out=cap_out::recommend(&data,&json!({"result":input})).unwrap();
    conserved(&out);
    assert_eq!(out["item_mode"],"planned");
    assert_eq!(out["item_budget"].as_array().unwrap().len(),3);
    assert_eq!(out["item_source_alternatives"][0]["alternatives"].as_array().unwrap().len(),3);
    assert!(ledger(&out["board"]).values().all(|name|["Spear of Shojin","Jeweled Gauntlet","Rabadon's Deathcap"].contains(&name.as_str())));
}

#[test]
fn ordinary_annotations_keep_published_suggestions_and_are_not_mutated_by_cap() {
    let data=fixture();
    let input=source(&data,starting()["champions"].clone());
    let original=input.clone();
    let builds=input["planned_item_builds"].as_array().unwrap();
    let mage=builds.iter().find(|build|build["target"]=="Ahri").unwrap();
    assert_eq!(mage["items"],resources::item_builds()["champions"]["Ahri"]["items"]);
    assert!(mage.get("item_ids").is_none());
    let _out=call(&data,&input,json!([])).unwrap();
    assert_eq!(input,original);
    let mut annotated=input.clone();
    guidance::annotate_result(&mut annotated,&data).unwrap();
    assert_eq!(annotated["planned_item_builds"],original["planned_item_builds"]);
}

#[test]
fn incompatible_locked_fighters_leave_released_mage_items_on_bench() {
    let data=fixture();
    let input=source(&data,json!(["Leona","Front","Filler B","Fighter A","Fighter B"]));
    let out=call(&data,&input,mage_inventory(json!("Ahri"))).unwrap();
    conserved(&out);
    assert!(out["board"]["planned_item_builds"].as_array().unwrap().is_empty());
    assert_eq!(out["board"]["item_bench"].as_array().unwrap().len(),3);
    assert_eq!(out["board"]["item_fit"]["score"],0.0);
    assert!(out["phases"].as_array().unwrap().iter().flat_map(|phase|phase["item_transfers"].as_array().unwrap())
        .any(|transfer|transfer["method"]=="sell_outgoing" && transfer["outgoing"]=="Ahri" && transfer["incoming"].is_null()));
}

fn tank_fixture(cheap:&str, premium:&str) -> GameData {
    let mut champions=vec![
        json!({"name":cheap,"cost":4,"traits":[],"role":"Attack Tank"}),
        json!({"name":premium,"cost":5,"traits":[],"role":"Magic Tank"}),
        json!({"name":"Front","cost":1,"traits":[],"role":"Attack Tank"})
    ];
    champions.extend(["A","B","C","D","E","F"].map(|suffix|
        json!({"name":format!("Filler {suffix}"),"cost":1,"traits":[],"role":"Attack Marksman"})));
    GameData::from_value(json!({"traits":[],"champions":champions})).unwrap()
}

fn tank_source(data:&GameData, cheap:&str, premium:&str, cheap_stars:u8, premium_stars:u8) -> Value {
    let names=vec![cheap.to_owned(),premium.to_owned(),"Filler A".into(),"Filler B".into(),"Filler C".into(),"Filler D".into(),"Filler F".into()];
    let mut ranks:BTreeMap<String,u8>=data.champions.keys().map(|name|(name.clone(),1)).collect();
    ranks.insert(cheap.to_owned(),cheap_stars);
    ranks.insert(premium.to_owned(),premium_stars);
    let context=json!({"size":7,"mode":"balanced","min_collab":0,"collab":[],
        "locked":[cheap,premium,"Filler A","Filler B","Filler C","Filler D","Filler F"],
        "excluded":[],"stars":ranks,"sona_band":""});
    let options=OptimizeOptions::from_value(&context).unwrap();
    let mut board=optimizer::evaluate_team(data,&names,&options.collab,&options.mode,&options.sona_band,&options.stars,Some(7)).unwrap();
    board["level"]=json!(7);
    guidance::annotate_result(&mut board,data).unwrap();
    board["planning_context"]=context;
    board["planned_item_builds"]=json!([
        {"kind":"tank","holder":cheap,"target":cheap,"items":["Warmog's Armor"]},
        {"kind":"tank","holder":premium,"target":premium,"items":["Warmog's Armor"]}
    ]);
    board
}

fn assert_main_tank(board:&Value, expected:&str) {
    assert_eq!(board["item_holders"]["tank"],expected);
    let marker=board["positioning"].as_array().unwrap().iter().find(|marker|marker["champion"]==expected).unwrap();
    assert_eq!(marker["row"],0);
    assert_eq!(marker["column"],4);
    assert!(marker["reason"].as_str().unwrap().contains("Main item-holder tank"));
}

fn assert_retained_tank_custody(out:&Value, expected:&str) {
    conserved(out);
    for board in std::iter::once(&out["from_board"]).chain(out["phases"].as_array().unwrap().iter().map(|phase|&phase["board"])).chain(std::iter::once(&out["board"])) {
        assert_main_tank(board,expected);
        for item in out["item_budget"].as_array().unwrap() {
            assert_eq!(holder(board,item["id"].as_str().unwrap()).as_deref(),item["source_holder"].as_str());
        }
    }
    assert!(out["phases"].as_array().unwrap().iter().all(|phase|phase["item_transfers"].as_array().unwrap().is_empty()));
    assert!(out["board"]["item_transfers"].as_array().is_none_or(|moves|moves.is_empty()));
}

#[test]
fn equipped_equal_rank_premium_tank_wins_independently_of_name_or_item_mode() {
    for (cheap,premium) in [("Garen","Rell"),("Zulu","Alpha")] {
        let data=tank_fixture(cheap,premium);
        let input=tank_source(&data,cheap,premium,2,2);
        let planned=cap_out::recommend(&data,&json!({"result":input})).unwrap();
        assert_eq!(planned["item_mode"],"planned");
        assert_retained_tank_custody(&planned,premium);
        let collected=cap_out::recommend(&data,&json!({"result":input,"item_inventory":[
            {"id":"cheap-armor","item":"Warmog's Armor","holder":cheap},
            {"id":"premium-armor","item":"Warmog's Armor","holder":premium}
        ]})).unwrap();
        assert_eq!(collected["item_mode"],"collected");
        assert_retained_tank_custody(&collected,premium);
    }
}

#[test]
fn main_tank_requires_actual_compatible_items_not_a_relabelled_carried_set() {
    let data=tank_fixture("Garen","Rell");
    let input=tank_source(&data,"Garen","Rell",2,2);
    for premium_items in [vec![],vec![json!({"id":"premium-shojin","item":"Spear of Shojin","holder":"Rell"})]] {
        let mut inventory=vec![json!({"id":"cheap-armor","item":"Warmog's Armor","holder":"Garen"})];
        inventory.extend(premium_items);
        let out=cap_out::recommend(&data,&json!({"result":input,"item_inventory":inventory})).unwrap();
        assert_retained_tank_custody(&out,"Garen");
        for board in std::iter::once(&out["from_board"]).chain(out["phases"].as_array().unwrap().iter().map(|phase|&phase["board"])).chain(std::iter::once(&out["board"])) {
            if holder(board,"premium-shojin").is_some() {
                assert_eq!(holder(board,"premium-shojin").as_deref(),Some("Rell"));
                assert!(board["planned_item_builds"].as_array().unwrap().iter().any(|build|
                    build["holder"]=="Rell" && build["items"]==json!(["Spear of Shojin"])));
            }
        }
    }
}

#[test]
fn equal_strength_main_tanks_use_actual_item_fit_then_stable_name() {
    let mut raw=tank_fixture("Garen","Rell").raw;
    raw["champions"][1]["cost"]=json!(4);
    let data=GameData::from_value(raw).unwrap();
    let input=tank_source(&data,"Garen","Rell",2,2);
    for (extra,expected) in [(false,"Garen"),(true,"Rell")] {
        let mut inventory=vec![
            json!({"id":"cheap-armor","item":"Warmog's Armor","holder":"Garen"}),
            json!({"id":"premium-armor","item":"Warmog's Armor","holder":"Rell"})
        ];
        if extra {inventory.push(json!({"id":"premium-vest","item":"Bramble Vest","holder":"Rell"}));}
        for reversed in [false,true] {
            let mut source=input.clone();
            if reversed {source["champions"].as_array_mut().unwrap().reverse();inventory.reverse();}
            let out=cap_out::recommend(&data,&json!({"result":source,"item_inventory":inventory})).unwrap();
            assert_retained_tank_custody(&out,expected);
        }
    }
}

#[test]
fn current_three_star_tank_beats_new_one_star_premium_until_explicit_upgrade() {
    let data=tank_fixture("Garen","Rell");
    let input=tank_source(&data,"Garen","Rell",3,3);
    let start=json!({"champions":["Garen","Front","Filler A","Filler B","Filler C","Filler D","Filler F"],"stars":{"Garen":3,"Front":2}});
    let out=cap_out::recommend(&data,&json!({"result":input,"starting_board":start,"item_inventory":[
        {"id":"retained-armor","item":"Warmog's Armor","holder":"Garen"},
        {"id":"retained-vest","item":"Bramble Vest","holder":"Garen"},
        {"id":"retained-stoneplate","item":"Gargoyle Stoneplate","holder":"Garen"},
        {"id":"released-armor","item":"Warmog's Armor","holder":"Front"}
    ]})).unwrap();
    conserved(&out);
    assert_main_tank(&out["from_board"],"Garen");
    assert_eq!(out["board"]["acquisition_stars"]["Rell"],1);
    let phases=out["phases"].as_array().unwrap();
    let acquired=phases.iter().find(|phase|phase["added"].as_array().unwrap().contains(&json!("Rell"))).unwrap();
    assert_eq!(acquired["board"]["planned_stars"]["Garen"],3);
    assert_eq!(acquired["board"]["planned_stars"]["Rell"],1);
    assert!(acquired["purchases"].as_array().unwrap().iter().any(|purchase|purchase["champion"]=="Rell" && purchase["stars"]==1));
    assert_main_tank(&acquired["board"],"Garen");
    let upgrade=phases.iter().find(|phase|phase["star_upgrades"].as_array().unwrap().iter().any(|upgrade|upgrade["champion"]=="Rell")).unwrap();
    assert_eq!(upgrade["kind"],"star_upgrade");
    assert_eq!(upgrade["board"]["planned_stars"]["Rell"],3);
    assert_main_tank(&upgrade["board"],"Rell");
    assert_main_tank(&out["board"],"Rell");
    for board in std::iter::once(&out["from_board"]).chain(phases.iter().map(|phase|&phase["board"])).chain(std::iter::once(&out["board"])) {
        assert_eq!(holder(board,"retained-armor").as_deref(),Some("Garen"));
        assert_eq!(holder(board,"retained-vest").as_deref(),Some("Garen"));
        assert_eq!(holder(board,"retained-stoneplate").as_deref(),Some("Garen"));
        let previous_present=board["champions"].as_array().unwrap().contains(&json!("Front"));
        assert_eq!(holder(board,"released-armor").as_deref(),Some(if previous_present {"Front"} else {"Rell"}));
    }
    let moves:Vec<_>=phases.iter().flat_map(|phase|phase["item_transfers"].as_array().unwrap()).collect();
    assert_eq!(moves.len(),1);
    assert_eq!(moves[0]["item_ids"],json!(["released-armor"]));
    assert_eq!(moves[0]["outgoing"],"Front");
    assert_eq!(moves[0]["incoming"],"Rell");
    assert_eq!(moves[0]["method"],"sell_outgoing");
}
