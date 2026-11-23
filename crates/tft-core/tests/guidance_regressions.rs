use serde_json::{json,Value};
use tft_core::{guidance,model::GameData,positioning,trait_support};
fn data()->GameData{GameData::from_value(json!({"champions":[{"name":"Amumu","cost":1,"role":"Magic Tank","traits":["A"],"attack_range":1},{"name":"Tank","cost":4,"role":"Attack Tank","traits":["A"]},{"name":"Carry","cost":1,"role":"Attack Marksman","traits":["A"]},{"name":"Premium","cost":5,"role":"Attack Marksman","traits":["A"]},{"name":"Yone","cost":5,"role":"Attack Fighter","traits":["A"],"attack_range":1},{"name":"Baron","cost":5,"slots":2,"role":"Attack Tank","traits":["A"]},{"name":"Teemo","cost":2,"role":"Magic Caster","traits":["A"]}],"traits":[{"name":"A","breakpoints":[2,4],"unique":false}]})).unwrap()}
fn board(ns: &[&str])->Value{json!({"champions":ns,"traits":[{"name":"A","count":ns.len()}],"planned_stars":{}})}
#[test]fn weighted_exchange_and_role_first(){let d=data();let s=|ns:&[&str]|ns.iter().map(|s|s.to_string()).collect::<Vec<_>>();assert_eq!(guidance::replacement_priority(&s(&["Amumu","Tank"]),&s(&["Baron"]),&d),2*(1+d.champions.values().map(|c|c.slots as i64).sum::<i64>())+1);assert!(guidance::replacement_priority(&s(&["Carry"]),&s(&["Premium"]),&d)>guidance::replacement_priority(&s(&["Carry"]),&s(&["Tank"]),&d));}
#[test]fn upgraded_holder_and_retained_transfer(){let d=data();let mut first=board(&["Carry"]);first["planned_stars"]=json!({"Carry":3});let mut r=board(&["Carry","Premium"]);r["buildup"]=json!([{"board":first},{"board":board(&["Carry","Premium"])}]);guidance::annotate_result(&mut r,&d).unwrap();assert_eq!(r["buildup"][0]["board"]["item_holders"]["ad"],"Carry");assert_eq!(r["buildup"][1]["item_transfers"][0]["method"],"remover_or_later_sale");}
#[test]fn isolation_quest_and_nested_annotations(){let d=data();let original=d.raw.clone();let mut r=board(&["Amumu","Yone","Teemo","Baron"]);let mut prep=board(&["Amumu","Yone","Teemo"]);prep["positioning_constraint"]=json!("one_front_two_rows");r["buildup"]=json!([{"board":board(&["Amumu","Yone","Teemo"]),"preparation_boards":[{"board":prep}],"goal_board":board(&["Baron","Teemo"]),"upgrades":[{"board":board(&["Tank","Teemo"])}],"fallback":board(&["Carry"])}]);let roster=r["champions"].clone();guidance::annotate_result(&mut r,&d).unwrap();positioning::annotate_positioning(&mut r,&d).unwrap();trait_support::annotate_trait_support(&mut r,&d).unwrap();assert_eq!(r["champions"],roster);assert_eq!(d.raw,original);let p=r["buildup"][0]["preparation_boards"][0]["board"]["positioning"].as_array().unwrap();assert_eq!(p.iter().filter(|m|m["row"].as_u64().unwrap()<2).count(),1);assert_eq!(r["positioning"].as_array().unwrap().iter().filter(|m|m["champion"]=="Baron").count(),1);let b=&r["buildup"][0]["board"];let markers=b["positioning"].as_array().unwrap();let amumu=markers.iter().find(|m|m["champion"]=="Amumu").unwrap();assert_eq!((amumu["row"].as_u64(),amumu["column"].as_u64()),(Some(0),Some(3)));assert_eq!(b["champion_trait_support"]["Yone"]["percent"],50.0);}

fn late_data()->GameData {
    GameData::from_value(json!({"champions":[
        {"name":"Tristana","cost":2,"role":"Attack Marksman","traits":[]},
        {"name":"Lucian","cost":5,"role":"Attack Marksman","traits":[]},
        {"name":"Teemo","cost":2,"role":"Magic Caster","traits":[]},
        {"name":"Premium AP","cost":5,"role":"Magic Caster","traits":[]},
        {"name":"Reroll Tank","cost":3,"role":"Magic Tank","traits":[]},
        {"name":"Weighted Tank","cost":4,"slots":8,"role":"Magic Tank","traits":[]},
        {"name":"Yone","cost":2,"role":"Attack Fighter","traits":[]}
    ],"traits":[]})).unwrap()
}
fn late_board(ns:&[&str],stars:Value)->Value {
    let mut b=board(ns);
    b["planned_stars"]=stars;
    b
}
#[test]
fn late_carry_prefers_premium_unless_fielded_reroll() {
    let d=late_data();
    for (stars,field_tank,expected) in [
        (json!({"Tristana":2}),false,"Lucian"),
        (json!({"Tristana":3}),false,"Tristana"),
        (json!({"Tristana":2,"Reroll Tank":3}),true,"Tristana"),
        (json!({"Tristana":2,"Reroll Tank":3}),false,"Lucian")
    ] {
        let ns=if field_tank{vec!["Tristana","Lucian","Reroll Tank"]}else{vec!["Tristana","Lucian"]};
        let mut r=late_board(&ns,stars);
        r["level"]=json!(10);
        guidance::annotate_result(&mut r,&d).unwrap();
        assert_eq!(r["item_holders"]["ad"],expected);
        if field_tank{assert_eq!(r["item_holders"]["tank"],"Reroll Tank")}
    }
}
#[test]
fn late_carry_uses_weighted_slots_not_star_map_length_and_keeps_fallback() {
    let d=late_data();
    let mut weighted=late_board(&["Tristana","Lucian","Weighted Tank"],json!({"Tristana":2}));
    guidance::annotate_result(&mut weighted,&d).unwrap();
    assert_eq!(weighted["item_holders"]["ad"],"Lucian");
    let mut early=late_board(&["Tristana","Lucian"],json!({"Tristana":2,"A":1,"B":1,"C":1,"D":1,"E":1,"F":1,"G":1,"H":1,"I":1}));
    guidance::annotate_result(&mut early,&d).unwrap();
    assert_eq!(early["item_holders"]["ad"],"Tristana");
    let mut forced=late_board(&["Tristana","Teemo"],json!({"Tristana":2}));
    forced["level"]=json!(10);
    guidance::annotate_result(&mut forced,&d).unwrap();
    assert_eq!(forced["item_holders"]["ad"],"Tristana");
    assert_eq!(forced["item_holders"]["ap"],"Teemo");
}
#[test]
fn late_final_omits_unsuitable_damage_categories() {
    let d=late_data();
    let mut r=late_board(&["Lucian","Teemo","Yone"],json!({"Teemo":2,"Yone":2}));
    r["level"]=json!(10);
    guidance::annotate_result(&mut r,&d).unwrap();
    assert_eq!(r["item_holders"]["ad"],"Lucian");
    assert!(r["item_holders"]["ap"].is_null());
    assert!(r["item_holders"]["flex"].is_null());
}
#[test]
fn late_arrival_and_preparation_keep_items_until_final_handoff() {
    let d=late_data();
    let final_board=late_board(&["Tristana","Lucian"],json!({"Tristana":2,"Lucian":2}));
    let early_board=late_board(&["Tristana","Lucian"],json!({"Tristana":2}));
    let arrival=late_board(&["Tristana"],json!({"Tristana":2}));
    let mut r=final_board.clone();
    r["buildup"]=json!([
        {"level":9,"board":early_board,"upgrades":[]},
        {"level":10,"board":arrival,"preparation_boards":[{"board":arrival}],
         "goal_board":final_board,"upgrades":[{"board":final_board}]}
    ]);
    guidance::annotate_result(&mut r,&d).unwrap();
    let early=&r["buildup"][0]["board"];
    let step=&r["buildup"][1];
    let phase=&step["upgrades"][0];
    for b in [early,&step["board"],&step["preparation_boards"][0]["board"]] {
        assert_eq!(b["item_holders"]["ad"],"Tristana");
        let build=b["planned_item_builds"].as_array().unwrap().iter().find(|b|b["kind"]=="ad").unwrap();
        assert_eq!(build["holder"],"Tristana");
        assert_eq!(build["target"],"Lucian");
    }
    assert!(step["item_transfers"].as_array().unwrap().is_empty());
    assert_eq!(phase["item_transfers"][0]["outgoing"],"Tristana");
    assert_eq!(phase["item_transfers"][0]["incoming"],"Lucian");
    assert_eq!(phase["item_transfers"][0]["method"],"remover_or_later_sale");
    for b in [&r,&step["goal_board"],&phase["board"]] {
        assert_eq!(b["item_holders"]["ad"],"Lucian");
        assert_eq!(b["item_holders"],r["item_holders"]);
        assert_eq!(b["planned_item_builds"],r["planned_item_builds"]);
    }
}
#[test]
fn late_temporary_other_profile_survives_until_compatible_upgrade() {
    let d=late_data();
    let arrival=late_board(&["Tristana","Lucian","Teemo"],json!({"Tristana":2,"Teemo":2}));
    let final_board=late_board(&["Tristana","Lucian","Teemo","Premium AP"],json!({"Tristana":2,"Teemo":2}));
    let mut r=final_board.clone();
    r["buildup"]=json!([{"level":10,"board":arrival,"goal_board":final_board,
        "preparation_boards":[{"board":arrival}],
        "upgrades":[{"board":arrival},{"board":final_board}]}]);
    guidance::annotate_result(&mut r,&d).unwrap();
    let step=&r["buildup"][0];
    for b in [&step["board"],&step["preparation_boards"][0]["board"],&step["upgrades"][0]["board"]] {
        assert_eq!(b["item_holders"]["ap"],"Teemo");
        assert_eq!(b["item_holders"]["ad"],"Lucian");
    }
    let phase=&step["upgrades"][1];
    assert_eq!(phase["board"]["item_holders"]["ap"],"Premium AP");
    assert_eq!(phase["item_transfers"][0]["outgoing"],"Teemo");
    assert_eq!(phase["item_transfers"][0]["incoming"],"Premium AP");
    assert_eq!(phase["item_transfers"][0]["method"],"remover_or_later_sale");
}

fn routing_data()->GameData {
    GameData::from_value(json!({"champions":[
        {"name":"Lucian","cost":5,"role":"Attack Marksman","traits":[]},
        {"name":"Akali (K/DA)","cost":5,"role":"Attack Assassin","traits":[]},
        {"name":"Sett","cost":5,"role":"Attack Fighter","traits":[]},
        {"name":"Ahri","cost":5,"role":"Magic Caster","traits":[]},
        {"name":"Yone","cost":5,"role":"Attack Fighter","traits":[]},
        {"name":"Amumu","cost":1,"role":"Magic Tank","traits":[]},
        {"name":"Tank","cost":4,"role":"Attack Tank","traits":[]},
        {"name":"Weak Marksman","cost":1,"role":"Attack Marksman","traits":[]},
        {"name":"Weak Assassin","cost":1,"role":"Attack Assassin","traits":[]},
        {"name":"Weak Fighter","cost":1,"role":"Attack Fighter","traits":[]},
        {"name":"Generic AD","cost":5,"role":"Attack Caster","traits":[]},
        {"name":"Generic AP","cost":5,"role":"Magic Caster","traits":[]},
        {"name":"Magic Fighter","cost":5,"role":"Magic Fighter","traits":[]},
        {"name":"Unknown","cost":5,"role":"Unverified","traits":[]},
        {"name":"Jhin","cost":1,"role":"Attack Specialist","traits":[]},
        {"name":"Xayah","cost":1,"role":"Attack Caster","traits":[]},
        {"name":"Gnar","cost":1,"role":"Attack Specialist","traits":[],"attack_range":4},
        {"name":"Smeech","cost":1,"role":"Attack Specialist","traits":[],"attack_range":4},
        {"name":"Akali (True Damage)","cost":1,"role":"Magic Assassin","traits":[]}
    ],"traits":[]})).unwrap()
}
fn build<'a>(board:&'a Value,kind:&str)->&'a Value {
    board["planned_item_builds"].as_array().unwrap().iter().find(|build|build["kind"]==kind).unwrap()
}
fn routed(target:&str,temporary:Value)->Value {
    let d=routing_data();
    let mut result=late_board(&[target],json!({target:2}));
    result["buildup"]=json!([{"level":8,"board":temporary}]);
    guidance::annotate_result(&mut result,&d).unwrap();
    result
}
#[test]
fn attack_speed_routes_real_holders_and_all_nested_boards() {
    let d=routing_data();
    let temporary=late_board(&["Weak Marksman","Generic AD","Generic AP"],json!({"Generic AD":3,"Generic AP":3}));
    let final_board=late_board(&["Lucian"],json!({"Lucian":2}));
    let mut result=final_board.clone();
    result["buildup"]=json!([
        {"level":7,"board":temporary,"fallback":temporary,"goal_board":temporary,
         "preparation_boards":[{"board":temporary}],"upgrades":[{"board":temporary}]},
        {"level":8,"board":final_board,"goal_board":final_board,"upgrades":[{"board":final_board}]}
    ]);
    guidance::annotate_result(&mut result,&d).unwrap();
    let step=&result["buildup"][0];
    for board in [&step["board"],&step["fallback"],&step["goal_board"],&step["preparation_boards"][0]["board"],&step["upgrades"][0]["board"]] {
        assert_eq!(board["item_holders"]["ad"],"Weak Marksman");
        let set=build(board,"ad");
        assert_eq!(set["target"],"Lucian");
        assert_eq!(set["target_archetype"],"attack_speed");
        assert_eq!(set["holder_archetype"],"attack_speed");
        assert_eq!(set["holder_match"],"same_archetype");
        assert_eq!(set["holder"],board["item_holders"]["ad"]);
    }
    let transfer=&result["buildup"][1]["item_transfers"][0];
    assert_eq!(transfer["outgoing"],"Weak Marksman");
    assert_eq!(transfer["incoming"],"Lucian");
    assert_eq!(transfer["method"],"sell_outgoing");
    assert!(transfer["condition"].as_str().unwrap().contains("sell outgoing Weak Marksman"));
    for final_copy in [&result,&result["buildup"][1]["board"],&result["buildup"][1]["goal_board"],&result["buildup"][1]["upgrades"][0]["board"]] {
        assert_eq!(final_copy["item_holders"],result["item_holders"]);
        assert_eq!(final_copy["planned_item_builds"],result["planned_item_builds"]);
        assert_eq!(build(final_copy,"ad")["holder_match"],"final");
    }
    let before=result.clone();
    guidance::annotate_result(&mut result,&d).unwrap();
    assert_eq!(result,before);
    let decoded:Value=serde_json::from_str(&serde_json::to_string(&result).unwrap()).unwrap();
    assert_eq!(decoded,result);
}
#[test]
fn melee_archetypes_precede_stronger_damage_fallbacks() {
    for (target,candidates,holder,label) in [
        ("Akali (K/DA)",vec!["Weak Assassin","Weak Fighter","Generic AD"],"Weak Assassin","same_archetype"),
        ("Akali (K/DA)",vec!["Weak Fighter","Generic AD"],"Weak Fighter","related_melee"),
        ("Akali (K/DA)",vec!["Generic AD","Generic AP"],"Generic AD","damage_fallback"),
        ("Sett",vec!["Weak Fighter","Weak Assassin","Generic AD"],"Weak Fighter","same_archetype"),
        ("Sett",vec!["Weak Assassin","Generic AD"],"Weak Assassin","related_melee"),
        ("Sett",vec!["Generic AD","Generic AP"],"Generic AD","damage_fallback"),
    ] {
        let result=routed(target,late_board(&candidates,json!({"Generic AD":3,"Generic AP":3})));
        let temporary=&result["buildup"][0]["board"];
        assert_eq!(temporary["item_holders"]["ad"],holder);
        assert_eq!(build(temporary,"ad")["holder"],holder);
        assert_eq!(build(temporary,"ad")["holder_match"],label);
    }
    let result=routed("Akali (K/DA)",board(&["Generic AP","Magic Fighter","Unknown"]));
    let temporary=&result["buildup"][0]["board"];
    assert!(temporary["item_holders"]["ad"].is_null());
    assert!(build(temporary,"ad")["holder"].is_null());
    assert!(build(temporary,"ad")["holder_archetype"].is_null());
    assert_eq!(build(temporary,"ad")["holder_match"],"unassigned");
}
#[test]
fn sourced_items_classify_specialists_and_preserve_explicit_roles() {
    for holder in ["Jhin","Xayah"] {
        let result=routed("Lucian",late_board(&[holder,"Generic AD"],json!({"Generic AD":3})));
        assert_eq!(build(&result["buildup"][0]["board"],"ad")["holder"],holder);
        assert_eq!(build(&result["buildup"][0]["board"],"ad")["holder_archetype"],"attack_speed");
    }
    for holder in ["Gnar","Smeech"] {
        let result=routed("Sett",late_board(&[holder,"Generic AD"],json!({"Generic AD":3})));
        assert_eq!(build(&result["buildup"][0]["board"],"ad")["holder"],holder);
        assert_eq!(build(&result["buildup"][0]["board"],"ad")["holder_archetype"],"fighter");
    }
    // Sustain items do not turn an explicitly sourced assassin into a fighter.
    let result=routed("Ahri",board(&["Akali (True Damage)"]));
    assert_eq!(build(&result["buildup"][0]["board"],"ap")["holder_archetype"],"assassin");
}
#[test]
fn final_rank_controls_handoff_and_transfers_use_routed_holders() {
    let d=routing_data();
    let arrival=late_board(&["Lucian","Weak Marksman"],json!({"Weak Marksman":3}));
    let final_board=late_board(&["Lucian","Weak Marksman"],json!({"Lucian":2,"Weak Marksman":3}));
    let mut result=final_board.clone();
    result["buildup"]=json!([{"level":8,"board":arrival,"goal_board":final_board,"upgrades":[{"board":final_board}]}]);
    guidance::annotate_result(&mut result,&d).unwrap();
    let step=&result["buildup"][0];
    assert_eq!(step["board"]["item_holders"]["ad"],"Weak Marksman");
    assert_eq!(build(&step["board"],"ad")["holder_match"],"same_archetype");
    assert_eq!(step["upgrades"][0]["board"]["item_holders"]["ad"],"Lucian");
    let transfer=&step["upgrades"][0]["item_transfers"][0];
    assert_eq!(transfer["outgoing"],"Weak Marksman");
    assert_eq!(transfer["incoming"],"Lucian");
    assert_eq!(transfer["method"],"remover_or_later_sale");
    assert!(transfer["condition"].as_str().unwrap().contains("items do not move freely"));
}
#[test]
fn final_target_keeps_base_strength_and_chronology_ties() {
    let d=routing_data();
    let final_board=board(&["Lucian","Sett"]);
    let mut result=final_board.clone();
    result["buildup"]=json!([{"level":8,"board":board(&["Sett"]),"goal_board":board(&["Lucian"])}]);
    guidance::annotate_result(&mut result,&d).unwrap();
    assert_eq!(result["item_holders"]["ad"],"Sett");
    assert_eq!(build(&result["buildup"][0]["goal_board"],"ad")["target"],"Sett");
    assert_eq!(build(&result["buildup"][0]["goal_board"],"ad")["holder_match"],"damage_fallback");
    let mut stronger=late_board(&["Lucian","Sett"],json!({"Lucian":2}));
    stronger["buildup"]=json!([{"board":board(&["Weak Fighter"])}]);
    guidance::annotate_result(&mut stronger,&d).unwrap();
    assert_eq!(stronger["item_holders"]["ad"],"Lucian");
}
#[test]
fn late_guard_precedes_matching_and_tanks_keep_strength_ranking() {
    let d=routing_data();
    let mut result=late_board(&["Lucian","Tank"],json!({"Lucian":2}));
    let temporary=late_board(&["Weak Marksman","Generic AD","Amumu","Tank"],json!({"Amumu":2}));
    result["buildup"]=json!([{"level":10,"board":temporary}]);
    guidance::annotate_result(&mut result,&d).unwrap();
    assert_eq!(result["buildup"][0]["board"]["item_holders"]["ad"],"Generic AD");
    assert_eq!(result["buildup"][0]["board"]["item_holders"]["tank"],"Tank");
    let mut reroll=result.clone();
    reroll["buildup"][0]["board"]["planned_stars"]["Amumu"]=json!(3);
    guidance::annotate_result(&mut reroll,&d).unwrap();
    assert_eq!(reroll["buildup"][0]["board"]["item_holders"]["tank"],"Amumu");
    assert_eq!(reroll["buildup"][0]["board"]["item_holders"]["ad"],"Weak Marksman");
}
#[test]
fn ap_and_flex_keep_compatible_paths_without_free_transfers() {
    let result=routed("Ahri",board(&["Weak Marksman","Generic AP"]));
    assert_eq!(build(&result["buildup"][0]["board"],"ap")["holder"],"Generic AP");
    let result=routed("Yone",late_board(&["Weak Fighter","Generic AD","Generic AP"],json!({"Generic AD":3,"Generic AP":3})));
    assert_eq!(build(&result["buildup"][0]["board"],"flex")["holder"],"Weak Fighter");
    assert_eq!(result["buildup"][0]["board"]["item_holders"]["flex"],"Weak Fighter");
    for target in ["Lucian","Ahri"] {
        let d=routing_data();
        let mut result=late_board(&[target,"Yone"],json!({target:2}));
        let arrival=board(&["Yone"]);
        result["buildup"]=json!([
            {"level":8,"board":arrival},
            {"level":8,"board":late_board(&[target,"Yone"],json!({target:2}))}
        ]);
        guidance::annotate_result(&mut result,&d).unwrap();
        let kind=if target=="Lucian" {"ad"} else {"ap"};
        assert_eq!(build(&result["buildup"][0]["board"],kind)["holder"],"Yone");
        let transfer=result["buildup"][1]["item_transfers"].as_array().unwrap().iter().find(|transfer|transfer["kind"]==kind).unwrap();
        assert_eq!(transfer["outgoing"],"Yone");
        assert_eq!(transfer["incoming"],target);
        assert_eq!(transfer["method"],"remover_or_later_sale");
        assert!(transfer["condition"].as_str().unwrap().contains("alternatives"));
    }
}

#[test]
fn routed_flex_holder_cannot_accept_incompatible_ap_items() {
    let d=routing_data();
    let mut result=late_board(&["Yone","Ahri"],json!({}));
    result["buildup"]=json!([{"level":3,"board":board(&["Weak Fighter"])},
        {"level":10,"board":result.clone()}]);
    guidance::annotate_result(&mut result,&d).unwrap();
    let early=&result["buildup"][0]["board"];
    assert_eq!(early["item_holders"]["flex"],"Weak Fighter");
    assert!(build(early,"ap")["holder"].is_null());
    assert_eq!(build(early,"ap")["holder_match"],"unassigned");
    let transfers=result["buildup"][1]["item_transfers"].as_array().unwrap();
    assert_eq!(transfers.len(),1);
    assert_eq!(transfers[0]["kind"],"ad");
    assert_eq!(transfers[0]["outgoing"],"Weak Fighter");
    assert_eq!(transfers[0]["incoming"],"Yone");
}

#[test]
fn exact_final_snapshots_agree_even_at_earlier_levels() {
    let d=late_data();
    let final_board=late_board(&["Tristana","Lucian"],json!({"Tristana":2}));
    let mut result=final_board.clone();
    result["level"]=json!(10);
    result["buildup"]=json!([
        {"level":8,"board":final_board,"fallback":final_board,
         "goal_board":final_board,"preparation_boards":[{"board":final_board}],
         "upgrades":[{"board":final_board}]},
        {"level":10,"board":final_board,"goal_board":final_board,"upgrades":[{"board":final_board}]}
    ]);
    guidance::annotate_result(&mut result,&d).unwrap();
    assert_eq!(result["item_holders"]["ad"],"Lucian");
    for step in result["buildup"].as_array().unwrap() {
        for board in [&step["board"],&step["goal_board"],&step["upgrades"][0]["board"]] {
            assert_eq!(board["item_holders"],result["item_holders"]);
            assert_eq!(board["planned_item_builds"],result["planned_item_builds"]);
        }
    }
    let early=&result["buildup"][0];
    for board in [&early["fallback"],&early["preparation_boards"][0]["board"]] {
        assert_eq!(board["item_holders"],result["item_holders"]);
        assert_eq!(board["planned_item_builds"],result["planned_item_builds"]);
    }
}

#[test]
fn next_level_continues_from_last_phase_without_duplicate_item_handoff() {
    let d=routing_data();
    let mut result=late_board(&["Lucian"],json!({"Lucian":2}));
    result["buildup"]=json!([
        {"level":7,"board":board(&["Generic AD"]),"upgrades":[{"board":board(&["Weak Marksman"])}]},
        {"level":8,"board":board(&["Weak Marksman"])}
    ]);
    guidance::annotate_result(&mut result,&d).unwrap();
    assert_eq!(result["buildup"][0]["upgrades"][0]["item_transfers"][0]["incoming"],"Weak Marksman");
    assert_eq!(result["buildup"][1]["board"]["item_holders"]["ad"],"Weak Marksman");
    assert_eq!(result["buildup"][1]["item_transfers"],json!([]));
    assert_eq!(result["buildup"][1]["replacements"],json!([]));
}
