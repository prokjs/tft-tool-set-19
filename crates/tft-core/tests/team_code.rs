use serde_json::{json, Value};
use tft_core::team_code::{encode_team, decode_team, encode_team_with_mapping as encode, decode_team_with_mapping as decode};

const CODE: &str = "0235801a03435d35b332025336331000TFTSet16";
fn mapping() -> Value { json!({"set_id":"TFTSet16","format_version":"02","slots":10,"id_hex_digits":3,"champions":{"Kindred":0x358,"Skarner":0x01a,"Lucian & Senna":0x034,"Shyvana":0x35d,"Fiddlesticks":0x35b,"Ambessa":0x332,"Swain":0x025,"Taric":0x336,"Draven":0x331}}) }
fn names() -> Vec<String> { ["Kindred","Skarner","Lucian & Senna","Shyvana","Fiddlesticks","Ambessa","Swain","Taric","Draven"].map(str::to_owned).to_vec() }

#[test]
fn published_literal_and_empty_roster() {
    assert_eq!(encode(&names(),&mapping()).unwrap(),CODE);
    assert_eq!(decode(CODE,&mapping()).unwrap(),names());
    let empty = format!("02{}TFTSet16","000".repeat(10));
    assert_eq!(encode(&[],&mapping()).unwrap(),empty);
    assert!(decode(&empty,&mapping()).unwrap().is_empty());
    assert_eq!(decode(&format!("  {CODE}\n"),&mapping()).unwrap(),names());
}
#[test]
fn padding_uppercase_and_boundaries() {
    let mut map = mapping();
    map["champions"] = json!({"Low":1,"High":4095});
    assert_eq!(encode(&["Low".into(),"High".into()],&map).unwrap(),format!("02001fff{}TFTSet16","000".repeat(8)));
    assert_eq!(decode(&format!("02001FFF{}TFTSet16","000".repeat(8)),&map).unwrap(),["Low","High"]);
    assert_eq!(decode(&format!("0235800001a{}TFTSet16","000".repeat(7)),&mapping()).unwrap(),["Kindred","Skarner"]);
}
#[test]
fn roster_limits_duplicates_and_unknowns_are_errors() {
    let mut map = mapping();
    map["champions"]["Tenth"] = json!(4095);
    let mut roster = names(); roster.push("Tenth".into());
    assert_eq!(decode(&encode(&roster,&map).unwrap(),&map).unwrap(),roster);
    roster.push("Eleventh".into());
    assert!(encode(&roster,&map).unwrap_err().contains("nothing was truncated"));
    assert!(encode(&["Kindred".into(),"Kindred".into()],&map).is_err());
    assert!(encode(&["Kindred".into(),"Unknown".into()],&map).is_err());
    for prefix in ["358358","fff000"] { assert!(decode(&format!("02{prefix}{}TFTSet16","000".repeat(8)),&mapping()).is_err()); }
}
#[test]
fn malformed_codes_and_mappings_never_guess() {
    for code in [CODE.replacen("02","01",1),CODE.replace("Set16","Set19"),CODE.replacen("358","xyz",1),CODE.replacen("358","35",1),CODE.replacen("358","0358",1)] { assert!(decode(&code,&mapping()).is_err()); }
    for id in [json!(0),json!(-1),json!(4096),json!(true),json!("358"),Value::Null] {
        let mut map = mapping(); map["champions"]["Kindred"] = id;
        assert!(encode(&names(),&map).is_err());
    }
    let mut collision = mapping(); collision["champions"]["Other"] = json!(0x358);
    assert!(decode(CODE,&collision).unwrap_err().contains("Ambiguous"));
    for (field,value) in [("format_version",json!("01")),("slots",json!(9)),("id_hex_digits",json!(4)),("set_id",json!("TFTSet0")),("set_id",json!("TFTSet19_")),("unsupported",json!([]))] {
        let mut map = mapping(); map[field] = value;
        assert!(encode(&[],&map).is_err());
    }
    let mut map = mapping(); map["unsupported"] = json!({"Kindred":{"reason":"collision"}});
    assert!(decode(CODE,&map).is_err());
}
#[test]
fn unsupported_collisions_and_bundled_roster_roundtrip() {
    let mut map = mapping();
    map["unsupported"] = json!({"Ambiguous A":{"reason":"collision","source_id":4095},"Ambiguous B":{"reason":"collision","source_id":4095}});
    assert!(encode(&["Ambiguous A".into()],&map).unwrap_err().contains("collision"));
    assert!(decode(&format!("02fff{}TFTSet16","000".repeat(9)),&map).unwrap_err().contains("Ambiguous A, Ambiguous B"));
    let bundled = tft_core::resources::team_mapping();
    assert_eq!(bundled["champions"].as_object().unwrap().len(),89);
    assert_eq!(bundled["unsupported"].as_object().unwrap().len(),5);
    for name in bundled["champions"].as_object().unwrap().keys() {
        assert_eq!(decode_team(&encode_team(&[name.clone()]).unwrap()).unwrap(),[name.clone()]);
    }
    for name in bundled["unsupported"].as_object().unwrap().keys() { assert!(encode_team(&[name.clone()]).is_err()); }
}
