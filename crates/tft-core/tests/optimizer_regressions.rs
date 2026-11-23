use serde_json::{json, Value};
use std::collections::BTreeMap;
use tft_core::{model::{GameData, OptimizeOptions}, optimizer::{evaluate_team, optimize, planned_stars}};

fn options(size:usize)->OptimizeOptions { serde_json::from_value(json!({"size":size,"min_collab":0,"mode":"balanced","limit":12})).unwrap() }
fn data(traits:Value,champions:Value)->GameData {GameData::from_value(json!({"traits":traits,"champions":champions})).unwrap()}
fn row<'a>(board:&'a Value,name:&str)->&'a Value {board["traits"].as_array().unwrap().iter().find(|r|r["name"]==name).unwrap()}
#[test]
fn late_carry_presence_is_capped_fielded_and_level_gated() {
    let d=data(json!([]),json!([
        {"name":"Cheap","cost":1,"traits":[],"role":"Attack Marksman"},
        {"name":"Carry","cost":3,"traits":[],"role":"Magic Caster"},
        {"name":"Other","cost":4,"traits":[],"role":"Attack Fighter"},
        {"name":"Core","cost":2,"traits":[],"role":"Magic Tank"}
    ]));
    let score=|names:&[&str],level,mode,stars:&BTreeMap<String,u8>| {
        evaluate_team(&d,&names.iter().map(|n|n.to_string()).collect::<Vec<_>>(),&[],mode,"",stars,Some(level)).unwrap()["score"].as_f64().unwrap()
    };
    // Isolate the carry bonus from the separate automatic reroll policy.
    let baseline=BTreeMap::from([("Cheap".into(),1),("Carry".into(),1),("Other".into(),1)]);
    assert_eq!(score(&["Carry"],10,"balanced",&baseline)-score(&["Cheap"],10,"balanced",&baseline),300.0);
    assert_eq!(score(&["Carry","Other"],10,"balanced",&baseline),320.0);
    assert_eq!(score(&["Carry"],9,"balanced",&baseline),score(&["Cheap"],9,"balanced",&baseline));
    assert_eq!(score(&["Carry"],10,"synergies",&baseline),score(&["Cheap"],10,"synergies",&baseline));
    let reroll=BTreeMap::from([("Core".into(),3),("Cheap".into(),1)]);
    assert_eq!(score(&["Cheap"],10,"balanced",&reroll),20.0);
    // Tank anchor and existing reroll-path tax remain independent of the capped bonus.
    assert_eq!(score(&["Cheap","Core"],10,"balanced",&reroll),700.0);
    let cheap_stars=BTreeMap::from([("Cheap".into(),3)]);
    assert_eq!(score(&["Cheap"],10,"balanced",&cheap_stars),220.0);
    assert_eq!(score(&["Carry"],10,"high_value",&baseline)-score(&["Cheap"],10,"high_value",&baseline),360.0);
    assert_eq!(score(&["Cheap"],10,"balanced",&BTreeMap::new()),220.0);
}
#[test]
fn level_ten_beam_prefers_an_eligible_carry_without_banning_cheap_units() {
    let mut cs:Vec<_>=(0..10).map(|i|json!({"name":format!("Cheap{i}"),"cost":1,"traits":[],"role":"Attack Marksman"})).collect();
    cs.push(json!({"name":"Carry","cost":3,"traits":[],"role":"Magic Caster"}));
    let d=data(json!([]),json!(cs));
    let mut o=options(10);o.limit=1;
    o.stars=d.champions.keys().map(|n|(n.clone(),1)).collect();
    let board=optimize(&d,&o).unwrap().remove(0);
    assert!(board["champions"].as_array().unwrap().contains(&json!("Carry")));
    assert_eq!(board["champions"].as_array().unwrap().len(),10);
    assert_eq!(board["score"],320.0);
    o.excluded=vec!["Carry".into()];
    assert_eq!(optimize(&d,&o).unwrap()[0]["score"],20.0);
}
#[test]
fn weighted_exclusive_suffix_and_exact_budget() {
    let d=data(json!([]),json!([
        {"name":"A","cost":1,"slots":2,"traits":[],"exclusive_group":"pair"},
        {"name":"B","cost":1,"slots":2,"traits":[],"exclusive_group":"pair"},
        {"name":"C","cost":1,"traits":[]}
    ]));
    assert!(optimize(&d,&options(4)).is_err());
    let boards=optimize(&d,&options(3)).unwrap();assert_eq!(boards.len(),2);
    assert_eq!(boards[0]["champions"],json!(["A","C"]));
    let mut o=options(3);o.locked=vec!["A".into(),"B".into()];assert!(optimize(&d,&o).is_err());
}
#[test]
fn dynamic_collab_featured_band_and_star_counts_are_deduplicated() {
    let d=data(json!([
        {"name":"Collab","breakpoints":[3,5],"unique":false},
        {"name":"Jazz","breakpoints":[2],"unique":false},
        {"name":"K/DA","breakpoints":[3,5,7],"unique":false,"count_mode":"stars"}
    ]),json!([
        {"name":"Sona","cost":5,"traits":["Collab","Jazz","Jazz","K/DA"]},
        {"name":"OffGroup","cost":1,"traits":["Collab","K/DA"]}
    ]));
    let mut o=options(2);o.locked=vec!["Sona".into(),"OffGroup".into()];o.sona_band="K/DA".into();o.stars.insert("Sona".into(),4);
    let result=optimize(&d,&o).unwrap().remove(0);assert_eq!(row(&result,"Collab")["count"],1);assert_eq!(row(&result,"Jazz")["count"],1);assert_eq!(row(&result,"K/DA")["count"],5);
}
#[test]
fn jazz_bridge_requires_both_fielded_endpoints() {
    let d=data(json!([
        {"name":"Collab","breakpoints":[3,5],"unique":false},
        {"name":"Jazz","breakpoints":[2],"unique":false},
        {"name":"Link","breakpoints":[2],"unique":false}
    ]),json!([
        {"name":"BaseJazz","cost":1,"traits":["Jazz"]},
        {"name":"JazzLink","cost":1,"traits":["Jazz","Link"]},
        {"name":"Pool","cost":1,"traits":["Link"]},
        {"name":"Filler","cost":1,"traits":[]}
    ]));
    let names=vec!["BaseJazz".into(),"JazzLink".into(),"Filler".into()];
    let a=evaluate_team(&d,&names,&["Pool".into()],"balanced","",&BTreeMap::new(),None).unwrap();
    let b=evaluate_team(&d,&names,&[],"balanced","",&BTreeMap::new(),None).unwrap();assert_eq!(a,b);
    let names=vec!["BaseJazz".into(),"JazzLink".into(),"Pool".into()];
    let a=evaluate_team(&d,&names,&["Pool".into()],"balanced","",&BTreeMap::new(),None).unwrap();
    let b=evaluate_team(&d,&names,&[],"balanced","",&BTreeMap::new(),None).unwrap();assert_eq!(a["score"].as_f64().unwrap()-b["score"].as_f64().unwrap(),40.0);
}
#[test]
fn tank_defaults_anchor_and_fielded_carry_readiness() {
    let mut champions=vec![json!({"name":"FourTank","cost":4,"traits":[],"role":"Magic Tank"}),json!({"name":"Carry","cost":4,"traits":["True Damage"]}),json!({"name":"Low","cost":1,"traits":["True Damage"]})];
    for i in 0..10 {champions.push(json!({"name":format!("F{i}"),"cost":1,"traits":[]}));}
    let d=data(json!([{"name":"True Damage","breakpoints":[2,4,5],"unique":false}]),json!(champions));
    assert_eq!(planned_stars(&d,10,&BTreeMap::new(),"").unwrap()["FourTank"],2);
    let mut names=vec!["Carry".into(),"Low".into()];names.extend((0..8).map(|i|format!("F{i}")));
    let plain=evaluate_team(&d,&names,&[],"balanced","",&BTreeMap::new(),Some(10)).unwrap();
    assert_eq!(plain["planned_stars"]["Carry"],2);
    assert_eq!(plain["score"],-770.0);
    let stars=BTreeMap::from([("Carry".into(),2)]);let ready=evaluate_team(&d,&names,&[],"balanced","",&stars,Some(10)).unwrap();assert_eq!(ready["score"],plain["score"]);
    let unready=evaluate_team(&d,&names,&[],"balanced","",&BTreeMap::from([("Carry".into(),1)]),Some(10)).unwrap();
    assert_eq!(plain["score"].as_f64().unwrap()-unready["score"].as_f64().unwrap(),71.5);
    names[0]="FourTank".into();let anchor=evaluate_team(&d,&names,&[],"balanced","",&stars,Some(10)).unwrap();assert_eq!(anchor["frontline_anchor"],"FourTank");assert!(anchor["strategy_notes"].as_array().unwrap().iter().any(|n|n.as_str().unwrap().contains("Automatic tank upgrade")));
}
#[test]
fn tied_results_are_deterministic_and_diverse() {
    let cs:Vec<_>="ABCDEF".chars().map(|c|json!({"name":c.to_string(),"cost":1,"traits":[]})).collect();let d=data(json!([]),json!(cs));let mut o=options(3);o.limit=4;
    let first=optimize(&d,&o).unwrap();assert_eq!(first,optimize(&d,&o).unwrap());assert_eq!(first.len(),4);assert!(first.iter().any(|r|r["champions"]==json!(["A","B","C"])));assert!(first.iter().any(|r|r["champions"]==json!(["D","E","F"])));
}

#[test]
fn explicit_three_star_cheap_frontline_keeps_anchor_without_suppressing_four_cost_upgrades() {
    for cost in 1..=3 {
        let mut roster=vec![
            json!({"name":"CheapTank","cost":cost,"traits":[],"role":"Magic Tank"}),
            json!({"name":"FourTank","cost":4,"traits":[],"role":"Magic Tank"}),
            json!({"name":"CheapCarry","cost":cost,"traits":[],"role":"Attack Marksman"}),
        ];
        for i in 0..10 {roster.push(json!({"name":format!("F{i}"),"cost":1,"traits":[]}));}
        let d=data(json!([]),json!(roster));
        let requested=BTreeMap::from([("CheapTank".into(),3)]);
        let stars=planned_stars(&d,10,&requested,"").unwrap();
        assert_eq!(stars["CheapTank"],3);
        assert_eq!(stars["FourTank"],2);
        let explicit=BTreeMap::from([("CheapTank".into(),3),("FourTank".into(),2)]);
        assert_eq!(planned_stars(&d,10,&explicit,"").unwrap()["FourTank"],2);
        assert_eq!(planned_stars(&d,10,&BTreeMap::from([("CheapCarry".into(),3)]),"").unwrap()["FourTank"],2);
        assert_eq!(planned_stars(&d,10,&BTreeMap::from([("CheapTank".into(),2)]),"").unwrap()["FourTank"],2);
        let mut names=vec!["CheapTank".into(),"FourTank".into()];
        names.extend((0..8).map(|i|format!("F{i}")));
        let board=evaluate_team(&d,&names,&[],"balanced","",&requested,Some(10)).unwrap();
        assert_eq!(board["frontline_anchor"],"CheapTank");
        assert_eq!(board["planned_stars"]["FourTank"],2);
        assert!(board["strategy_notes"].as_array().unwrap().iter().any(|note|note.as_str().unwrap().contains("Automatic tank upgrade")));
        let mut o=options(10);o.limit=1;o.locked=vec!["CheapTank".into()];o.stars=requested;
        let result=tft_core::planner::recommend_with_buildups(&d,&o).unwrap().remove(0);
        assert_eq!(result["frontline_anchor"],"CheapTank");
        assert_eq!(result["planned_stars"]["CheapTank"],3);
        let final_step=result["buildup"].as_array().unwrap().last().unwrap();
        assert_eq!(final_step["goal_board"]["frontline_anchor"],"CheapTank");
        assert_eq!(final_step["goal_board"]["planned_stars"]["CheapTank"],3);
    }
}

#[test]
fn kda_counts_fielded_star_targets_with_dynamic_sona_and_explicit_rerolls() {
    let d=data(json!([{"name":"K/DA","breakpoints":[3,5,7,10],"unique":false,"count_mode":"stars"},{"name":"Collab","breakpoints":[3,5],"unique":false}]),json!([
        {"name":"A","cost":1,"traits":["K/DA"]},
        {"name":"B","cost":2,"traits":["K/DA"]},
        {"name":"Absent","cost":1,"traits":["K/DA"]},
        {"name":"Sona","cost":7,"traits":[]}
    ]));
    let names=vec!["A".into(),"B".into(),"Sona".into()];
    let base=evaluate_team(&d,&names,&[],"balanced","K/DA",&BTreeMap::new(),Some(10)).unwrap();
    assert_eq!(row(&base,"K/DA")["count"],7);
    assert_eq!(row(&base,"K/DA")["tier"],7);
    assert_eq!(base["planned_stars"]["Sona"],1);
    let overrides=BTreeMap::from([("A".into(),1),("Absent".into(),4)]);
    let overridden=evaluate_team(&d,&names,&[],"balanced","K/DA",&overrides,Some(10)).unwrap();
    assert_eq!(row(&overridden,"K/DA")["count"],5);
    let reroll=BTreeMap::from([("A".into(),3),("B".into(),3)]);
    let lower=evaluate_team(&d,&names[..2],&[],"balanced","",&reroll,Some(8)).unwrap();
    assert_eq!(row(&lower,"K/DA")["count"],6);
    assert_eq!(planned_stars(&d,8,&BTreeMap::new(),"K/DA").unwrap()["A"],2);
    assert_eq!(planned_stars(&d,10,&BTreeMap::new(),"").unwrap()["Sona"],1);
    assert_eq!(tft_core::pool::pool(&d,&json!({"size":10,"sona_band":"K/DA"})).unwrap()["stars"]["Sona"],1);
}

#[test]
fn kda_late_defaults_and_lower_level_rerolls_reach_the_final_guide() {
    let mut cs=vec![json!({"name":"A","cost":1,"traits":["K/DA"]}),json!({"name":"B","cost":2,"traits":["K/DA"]})];
    for i in 0..8 {cs.push(json!({"name":format!("F{i}"),"cost":1,"traits":[]}));}
    let d=data(json!([{"name":"K/DA","breakpoints":[3,5,7],"unique":false,"count_mode":"stars"}]),json!(cs));
    for (size,stars,expected) in [(10,BTreeMap::new(),6),(8,BTreeMap::from([("A".into(),3),("B".into(),3)]),6)] {
        let mut o=options(size);o.locked=vec!["A".into(),"B".into()];o.stars=stars;o.limit=1;
        let result=tft_core::planner::recommend_with_buildups(&d,&o).unwrap().remove(0);
        assert_eq!(row(&result,"K/DA")["count"],expected);
        let last=result["buildup"].as_array().unwrap().last().unwrap();
        assert_eq!(last["goal_board"]["planned_stars"],result["planned_stars"]);
        assert_eq!(row(&last["goal_board"],"K/DA")["count"],expected);
        let late=result["buildup"].as_array().unwrap().iter().filter(|s|s["level"].as_u64().unwrap()>=6);
        for step in late {
            for phase in step["upgrades"].as_array().into_iter().flatten() {
                for upgrade in phase["star_upgrades"].as_array().into_iter().flatten() {
                    assert!(upgrade["to"].as_u64().unwrap()>upgrade["from"].as_u64().unwrap());
                }
            }
        }
    }
}

#[test]
fn universal_two_star_progression_is_cost_gated_and_manual_ranks_win() {
    let d=data(json!([{"name":"K/DA","breakpoints":[3,5,7],"unique":false,"count_mode":"stars"},{"name":"Collab","breakpoints":[3,5],"unique":false}]),json!([
        {"name":"One","cost":1,"traits":["K/DA"]},
        {"name":"Two","cost":2,"traits":[]},
        {"name":"Three","cost":3,"traits":[]},
        {"name":"Four","cost":4,"traits":[],"role":"Magic Tank"},
        {"name":"Five","cost":5,"traits":[]},
        {"name":"Sona","cost":7,"traits":[]}
    ]));
    for level in 1..=11 {
        let stars=planned_stars(&d,level,&BTreeMap::new(),"K/DA").unwrap();
        for (name,threshold) in [("One",5),("Two",6),("Three",7),("Four",8),("Five",10)] {
            assert_eq!(stars[name],if level>=threshold {2} else {1},"{name} at {level}");
        }
        assert_eq!(stars["Sona"],1);
    }
    let keepers=["One","Four","Five","Sona"].map(str::to_owned);
    let at_four=evaluate_team(&d,&keepers,&[],"balanced","K/DA",&BTreeMap::new(),Some(4)).unwrap();
    assert_eq!(at_four["planned_stars"]["One"],2);
    let explicit=BTreeMap::from([("One".into(),1),("Four".into(),3),("Sona".into(),4)]);
    let at_ten=evaluate_team(&d,&keepers,&[],"balanced","K/DA",&explicit,Some(10)).unwrap();
    assert_eq!(at_ten["planned_stars"]["One"],1);
    assert_eq!(at_ten["planned_stars"]["Four"],3);
    assert_eq!(at_ten["planned_stars"]["Sona"],4);
}

#[test]
fn normal_goal_rerolls_are_contextual_but_cap_out_never_auto_rerolls() {
    let d=data(json!([{"name":"Collab","breakpoints":[3,5],"unique":false}]),json!([
        {"name":"One","cost":1,"traits":[]},{"name":"Two","cost":2,"traits":[]},
        {"name":"Three","cost":3,"traits":[]},{"name":"Four","cost":4,"traits":[]},
        {"name":"Five","cost":5,"traits":[]},{"name":"Sona","cost":7,"traits":[]}
    ]));
    let normal=["One","Two","Three","Four","Five"].map(str::to_owned);
    for (level,name,expected) in [(4,"One",2),(5,"One",3),(5,"Two",1),(6,"Two",3),(6,"Three",1),(7,"Three",3)] {
        let board=evaluate_team(&d,&normal,&[],"balanced","",&BTreeMap::new(),Some(level)).unwrap();
        assert_eq!(board["planned_stars"][name],expected);
    }
    let sona_goal=["One","Three","Four","Five","Sona"].map(str::to_owned);
    let with_sona=evaluate_team(&d,&sona_goal,&[],"balanced","",&BTreeMap::new(),Some(10)).unwrap();
    assert_eq!(with_sona["planned_stars"]["One"],2);
    assert_eq!(with_sona["planned_stars"]["Three"],2);
    let unfielded=evaluate_team(&d,&sona_goal,&[],"balanced","",&BTreeMap::from([("Two".into(),3)]),Some(10)).unwrap();
    assert_eq!(unfielded["planned_stars"]["One"],2);
    assert_eq!(unfielded["planned_stars"]["Three"],2);
    let committed=evaluate_team(&d,&sona_goal,&[],"balanced","",&BTreeMap::from([("One".into(),3)]),Some(10)).unwrap();
    assert_eq!(committed["planned_stars"]["Three"],3);
    let cap=evaluate_team(&d,&normal,&[],"cap_out","",&BTreeMap::new(),Some(10)).unwrap();
    assert_eq!(cap["planned_stars"]["One"],2);
    assert_eq!(cap["planned_stars"]["Two"],2);
    assert_eq!(cap["planned_stars"]["Three"],2);
}

#[test]
fn returned_candidate_ranks_kda_counts_and_order_use_real_goal_defaults() {
    let d=data(json!([{"name":"K/DA","breakpoints":[3,5,7,10],"unique":false,"count_mode":"stars"}]),json!([
        {"name":"A","cost":1,"traits":["K/DA"]},{"name":"B","cost":2,"traits":["K/DA"]},
        {"name":"C","cost":3,"traits":["K/DA"]},{"name":"D","cost":4,"traits":[]},
        {"name":"E","cost":5,"traits":[]},{"name":"F","cost":1,"traits":[]}
    ]));
    let boards=optimize(&d,&options(5)).unwrap();
    for board in &boards {
        let count:u64=board["champions"].as_array().unwrap().iter().filter_map(Value::as_str)
            .filter(|n|d.champions[*n].traits.iter().any(|t|t=="K/DA"))
            .map(|n|board["planned_stars"][n].as_u64().unwrap()).sum();
        assert_eq!(row(board,"K/DA")["count"],count);
        if board["champions"].as_array().unwrap().contains(&json!("A")) {assert_eq!(board["planned_stars"]["A"],3);}
    }
    assert!(boards.windows(2).all(|pair|pair[0]["score"].as_f64().unwrap()>=pair[1]["score"].as_f64().unwrap()));
}

#[test]
fn goal_dependent_ranks_still_reject_unknown_manual_star_targets() {
    let d=data(json!([{"name":"Collab","breakpoints":[3,5],"unique":false}]),json!([
        {"name":"Sona","cost":7,"traits":[]},{"name":"Four","cost":4,"traits":[]}
    ]));
    let result=evaluate_team(&d,&["Sona".to_owned(),"Four".to_owned()],&[],"balanced","",
        &BTreeMap::from([("Unknown".into(),3)]),Some(10));
    assert!(result.unwrap_err().contains("unknown"));
}
