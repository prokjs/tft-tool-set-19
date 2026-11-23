use crate::model::{Champion, GameData};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
pub(crate) const KINDS: [&str;4] = ["tank","ad","ap","flex"];
pub fn item_profile(c:&Champion)-> &'static str {
    if c.name=="Yone" {return "flex"}
    if crate::model::is_tank(c) {return "tank"}
    let mut words=c.role.split_whitespace();
    let (Some(damage),Some(role),None)=(words.next(),words.next(),words.next()) else{return "unknown"};
    if !["assassin","caster","fighter","marksman","specialist"].iter().any(|word|role.eq_ignore_ascii_case(word)){return "unknown"}
    if damage.eq_ignore_ascii_case("attack"){"ad"}else if damage.eq_ignore_ascii_case("magic"){"ap"}else{"unknown"}
}
// Bit 0: eligible late carry; bit 1: cheap damage unit; bit 2: fielded reroll target.
pub(crate) fn carry_flags(c:&Champion,stars:u8)->u8 {
    let damage=matches!(item_profile(c),"ad"|"ap"|"flex");
    u8::from(damage && (c.cost>=3 || stars>=3))
        | (u8::from(damage && c.cost<=2)<<1)
        | (u8::from(c.cost<=3 && stars>=3)<<2)
}
pub(crate) fn has_late_carry(flags:u8)->bool {flags&1!=0 || flags&6==6}
pub fn planned_unit_strength(c:&Champion,stars:u8)->f64 { c.cost.max(1) as f64 * 3f64.powi(stars as i32-1) * if c.role.split_whitespace().any(|w|w.eq_ignore_ascii_case("specialist")) {0.6} else {1.0} }
fn compatible(a:&str,b:&str)->bool {a!="unknown" && b!="unknown" && (a==b || (a=="flex" && ["ad","ap"].contains(&b)) || (b=="flex" && ["ad","ap"].contains(&a)))}
pub(crate) fn names(b:&Value)->Vec<String> {b["champions"].as_array().map(|a|a.iter().filter_map(Value::as_str).map(str::to_owned).collect()).unwrap_or_default()}
pub(crate) fn visit_boards<F>(r:&mut Value, f:&mut F)->Result<(),String> where F:FnMut(&mut Value)->Result<(),String> {f(r)?; if let Some(steps)=r["buildup"].as_array_mut(){for s in steps {for k in ["board","fallback","goal_board"] {if s.get(k).is_some_and(|v|!v.is_null()){f(&mut s[k])?}} for k in ["upgrades","preparation_boards"] {if let Some(phases)=s[k].as_array_mut(){for p in phases {if p.get("board").is_some_and(|v|!v.is_null()){f(&mut p["board"])?}}}}}} Ok(())}
fn board_level(b:&Value,d:&GameData)->u64 {
    b["level"].as_u64().unwrap_or_else(||b["champions"].as_array().into_iter().flatten().filter_map(Value::as_str).map(|n|d.champions.get(n).map_or(1,|c|c.slots) as u64).sum())
}
fn board_identity(b:&Value)->Vec<(String,u64)> {
    b["champions"].as_array().into_iter().flatten().filter_map(Value::as_str).map(|n|(n.to_owned(),b["planned_stars"][n].as_u64().unwrap_or(1))).collect()
}
fn same_board(b:&Value,target:&[(String,u64)])->bool {
    b["champions"].as_array().is_some_and(|ns|ns.len()==target.len() && ns.iter().all(|n|n.as_str().is_some_and(|n|target.iter().any(|(name,stars)|name==n && *stars==b["planned_stars"][n].as_u64().unwrap_or(1)))))
}
#[derive(Clone,Copy)]
struct Descriptor {
    profile: &'static str,
    archetype: &'static str,
    cost: usize,
    specialist: bool,
}
type Descriptors<'a> = HashMap<&'a str,Descriptor>;
#[derive(Clone)]
struct Target {
    name: String,
    stars: u64,
    archetype: &'static str,
}
type Targets = [Option<Target>;4];
pub(crate) fn item_archetype(c:&Champion)->&'static str {
    if item_profile(c)=="tank" {return "tank"}
    let role=|name:&str|c.role.split_whitespace().any(|word|word.eq_ignore_ascii_case(name));
    if role("assassin") {return "assassin"}
    if role("fighter") {return "fighter"}
    let items=&crate::resources::item_builds()["champions"][&c.name]["items"];
    let has=|name:&str|items.as_array().is_some_and(|items|items.iter().any(|item|item.as_str()==Some(name)));
    if role("marksman") || has("Guinsoo's Rageblade") || has("Kraken's Fury") {return "attack_speed"}
    if role("specialist") && has("Bloodthirster") && (has("Sterak's Gage") || has("Titan's Resolve")) {return "fighter"}
    if role("caster") {return "caster"}
    "general"
}
pub(crate) fn archetype_match(name:&str,stars:u64,archetype:&str,target:&str,target_stars:u64,target_archetype:&str)->u8 {
    if name==target && stars>=target_stars {4}
    else if archetype!="general" && archetype==target_archetype {3}
    else if matches!((archetype,target_archetype),("assassin","fighter")|("fighter","assassin")) {2}
    else {1}
}
pub(crate) fn match_label(priority:u8)->&'static str {
    match priority {4=>"final",3=>"same_archetype",2=>"related_melee",1=>"damage_fallback",_=>"unassigned"}
}
fn descriptor(c:&Champion)->Descriptor {
    Descriptor {profile:item_profile(c),archetype:item_archetype(c),cost:c.cost,
        specialist:c.role.split_whitespace().any(|word|word.eq_ignore_ascii_case("specialist"))}
}
fn descriptors(data:&GameData)->Descriptors<'_> {
    data.champions.values().map(|c|(c.name.as_str(),descriptor(c))).collect()
}
struct Candidate<'a> {
    name: &'a str,
    descriptor: Descriptor,
    stars: u64,
    flags: u8,
    strength: f64,
}
fn select_holders(b:&Value,lookup:impl Fn(&str)->Option<Descriptor>,prev:Option<&Value>,level:u64,final_board:bool,targets:Option<&Targets>)->Value {
    let units:Vec<_>=b["champions"].as_array().into_iter().flatten().filter_map(Value::as_str).filter_map(|name|{
        let descriptor=lookup(name)?;
        let stars=b["planned_stars"][name].as_u64().unwrap_or(1);
        let damage=matches!(descriptor.profile,"ad"|"ap"|"flex");
        let flags=u8::from(damage && (descriptor.cost>=3 || stars>=3))
            | (u8::from(damage && descriptor.cost<=2)<<1)
            | (u8::from(descriptor.cost<=3 && stars>=3)<<2);
        Some(Candidate{name,descriptor,stars,flags,strength:descriptor.cost.max(1) as f64 * 3f64.powi(stars as i32-1) * if descriptor.specialist {0.6} else {1.0}})
    }).collect();
    let flags=units.iter().fold(0,|flags,u|flags|u.flags);
    let prefer_late=level>=10 && flags&4==0 && has_late_carry(flags);
    let mut out=json!({"tank":null,"ad":null,"ap":null,"flex":null});
    for (index,kind) in KINDS.into_iter().enumerate() {
        let target=targets.and_then(|targets|targets[index].as_ref());
        // Exact final copies share the frozen selection, including absent categories.
        if final_board && targets.is_some() {
            if let Some(target)=target {out[kind]=json!(target.name)}
            continue
        }
        let mut best:Option<&Candidate<'_>>=None;
        let previous=prev.and_then(|v|v[kind].as_str());
        let anchor=b["frontline_anchor"].as_str();
        let eligible=|u:&Candidate<'_>|prefer_late && kind!="tank" && u.flags&1!=0;
        let priority=|u:&Candidate<'_>|if kind=="tank" {0} else {target.map_or(0,|t|archetype_match(u.name,u.stars,u.descriptor.archetype,&t.name,t.stars,t.archetype))};
        for unit in &units {
            let profile=unit.descriptor.profile;
            let compatible_profile=profile==kind || (kind=="flex" && target.is_some() && compatible(profile,"flex"));
            if !compatible_profile || (prefer_late && final_board && kind!="tank" && unit.flags&1==0) {continue}
            if best.is_none_or(|current|{
                eligible(unit).cmp(&eligible(current))
                    .then_with(||priority(unit).cmp(&priority(current)))
                    .then_with(||unit.strength.total_cmp(&current.strength))
                    .then_with(||(previous==Some(unit.name)).cmp(&(previous==Some(current.name))))
                    .then_with(||(kind=="tank" && anchor==Some(unit.name)).cmp(&(kind=="tank" && anchor==Some(current.name))))
                    .then_with(||current.name.cmp(unit.name)).is_gt()
            }) {best=Some(unit)}
        }
        if let Some(unit)=best {out[kind]=json!(unit.name)}
    }
    out
}
fn holders(b:&mut Value,descriptors:&Descriptors<'_>,prev:Option<&Value>,level:u64,final_board:bool,targets:&Targets) {
    b["item_holders"]=select_holders(b,|name|descriptors.get(name).copied(),prev,level,final_board,Some(targets));
}
pub(crate) fn progression_holders(board:&Value,data:&GameData)->Value {
    select_holders(board,|name|data.champions.get(name).map(descriptor),None,4,false,None)
}
fn freeze_targets(result:&Value,data:&GameData,descriptors:&Descriptors<'_>,root_level:u64,root_target:&[(String,u64)])->Targets {
    // Read-only base-ranking chronology preserves strength/retained-holder ties without
    // allowing temporary archetype preferences to change the final carry selection.
    let mut previous=None;
    if let Some(steps)=result["buildup"].as_array() {
        for step in steps {
            let level=step["level"].as_u64().unwrap_or_else(||board_level(&step["board"],data));
            let goal_target=(!step["goal_board"].is_null()).then(||board_identity(&step["goal_board"]));
            let target=goal_target.as_deref().unwrap_or(root_target);
            let selected=(!step["board"].is_null()).then(||select_holders(&step["board"],|name|descriptors.get(name).copied(),previous.as_ref(),level,same_board(&step["board"],target),None));
            let mut phase_previous=selected;
            if let Some(phases)=step["upgrades"].as_array() {
                for phase in phases {
                    phase_previous=(!phase["board"].is_null()).then(||select_holders(&phase["board"],|name|descriptors.get(name).copied(),phase_previous.as_ref(),level,same_board(&phase["board"],target),None));
                }
            }
            previous=phase_previous;
        }
    }
    let selected=select_holders(result,|name|descriptors.get(name).copied(),previous.as_ref(),root_level,true,None);
    std::array::from_fn(|index|selected[KINDS[index]].as_str().map(|name|Target {
        name:name.to_owned(),stars:result["planned_stars"][name].as_u64().unwrap_or(1),
        archetype:descriptors[name].archetype,
    }))
}
#[derive(Clone)]
struct Exchange {old:u64,new:u64,score:[i64;4],signature:(Vec<String>,Vec<String>),record:Value}
#[derive(Clone)]
struct Matched {score:[i64;4], signatures:Vec<(Vec<String>,Vec<String>)>,records:Vec<Value>}
fn subsets(names:&[String],d:&GameData,min:usize,max:usize)->Vec<Vec<usize>> {fn walk(ns:&[String],d:&GameData,i:usize,total:usize,min:usize,max:usize,chosen:&mut Vec<usize>,out:&mut Vec<Vec<usize>>){if total>max{return} if i==ns.len(){if total>=min && !chosen.is_empty(){out.push(chosen.clone())}return} walk(ns,d,i+1,total,min,max,chosen,out);chosen.push(i);walk(ns,d,i+1,total+d.champions.get(&ns[i]).map_or(1,|c|c.slots),min,max,chosen,out);chosen.pop();} let mut out=vec![];walk(names,d,0,0,min,max,&mut vec![],&mut out);out}
fn replacement_plan(prev:&Value,cur:&Value,d:&GameData)->([i64;2],Vec<Value>){let p:BTreeSet<_>=names(prev).into_iter().collect();let c:BTreeSet<_>=names(cur).into_iter().collect();let old:Vec<_>=p.difference(&c).cloned().collect();let new:Vec<_>=c.difference(&p).cloned().collect();if old.is_empty(){return ([0,0],vec![])} let slots=|n:&String|d.champions.get(n).map_or(1,|c|c.slots);let profile=|n:&String|d.champions.get(n).map_or("unknown",item_profile);let role=|n:&String|d.champions.get(n).map(|c|c.role.to_lowercase().split_whitespace().map(str::to_owned).collect::<Vec<_>>()).unwrap_or_default(); let growth=new.iter().map(slots).sum::<usize>().saturating_sub(old.iter().map(slots).sum());let mut candidates=BTreeMap::new();let mut add=|oi:Vec<usize>,ni:Vec<usize>|{let on:Vec<_>=oi.iter().map(|i|old[*i].clone()).collect();let nn:Vec<_>=ni.iter().map(|i|new[*i].clone()).collect();let budget=on.iter().map(slots).sum::<usize>();let pairs:Vec<_>=if on.len()==1 {nn.iter().map(|n|(&on[0],n,slots(n))).collect()}else{on.iter().map(|o|(o,&nn[0],slots(o))).collect()};let same=pairs.iter().all(|(a,b,_)|compatible(profile(a),profile(b)));let comp=pairs.iter().filter(|(a,b,_)|compatible(profile(a),profile(b))).map(|x|x.2).sum::<usize>().min(budget);let exact=pairs.iter().filter(|(a,b,_)|compatible(profile(a),profile(b))&&role(a)==role(b)).map(|x|x.2).sum::<usize>().min(budget);let single=on.len()==1&&nn.len()==1;let label=if single&&slots(&on[0])==slots(&nn[0]) {if same {if profile(&on[0])=="flex" {profile(&nn[0])}else{profile(&on[0])}}else{"roster"}}else{"slot_exchange"};let om=oi.iter().fold(0u64,|m,i|m|1u64<<i);let nm=ni.iter().fold(0u64,|m,i|m|1u64<<i);let score=[comp as i64,exact as i64,budget as i64-nn.iter().map(slots).sum::<usize>() as i64,single as i64];let record=json!({"outgoing":on,"incoming":nn,"same_role":same,"profile":label});candidates.insert((om,nm),Exchange{old:om,new:nm,score,signature:(on,nn),record});}; for (i,n) in old.iter().enumerate(){for group in subsets(&new,d,slots(n),slots(n)+growth){add(vec![i],group)}} for(i,n)in new.iter().enumerate(){for group in subsets(&old,d,slots(n).saturating_sub(growth).max(1),slots(n)){add(group,vec![i])}}
fn solve(om:u64,nm:u64,cs:&[Exchange],memo:&mut HashMap<(u64,u64),Option<Matched>>)->Option<Matched>{if om==0{return Some(Matched{score:[0;4],signatures:vec![],records:vec![]})}if let Some(v)=memo.get(&(om,nm)){return v.clone()}let first=1u64<<om.trailing_zeros();let mut best:Option<Matched>=None;for c in cs.iter().filter(|c|c.old&first!=0&&c.old&om==c.old&&c.new&nm==c.new){if let Some(mut tail)=solve(om^c.old,nm^c.new,cs,memo){for i in 0..4{tail.score[i]+=c.score[i]}tail.signatures.push(c.signature.clone());tail.signatures.sort();tail.records.push(c.record.clone());if best.as_ref().is_none_or(|b|tail.score>b.score||(tail.score==b.score&&tail.signatures<b.signatures)){best=Some(tail)}}}memo.insert((om,nm),best.clone());best}
let cs:Vec<_>=candidates.into_values().collect();if let Some(mut m)=solve((1u64<<old.len())-1,(1u64<<new.len())-1,&cs,&mut HashMap::new()){m.records.sort_by_key(|r|(names_of(r,"outgoing"),names_of(r,"incoming")));return ([m.score[0],m.score[1]],m.records)}let budget=old.iter().map(slots).sum();let mut groups=subsets(&new,d,budget,new.iter().map(slots).sum());groups.sort_by_key(|g|(g.iter().map(|i|slots(&new[*i])).sum::<usize>(),g.len(),g.iter().map(|i|new[*i].clone()).collect::<Vec<_>>()));let additions=groups.first().map(|g|g.iter().map(|i|new[*i].clone()).collect::<Vec<_>>()).unwrap_or(new);let label=if old.iter().chain(&additions).any(|n|slots(n)!=1){"slot_exchange"}else{"roster"};([0,0],vec![json!({"outgoing":old,"incoming":additions,"same_role":false,"profile":label})])}
fn names_of(v:&Value,k:&str)->Vec<String>{v[k].as_array().map(|a|a.iter().filter_map(Value::as_str).map(str::to_owned).collect()).unwrap_or_default()}
pub fn replacement_priority(outgoing:&[String],incoming:&[String],data:&GameData)->i64{let(s,_)=replacement_plan(&json!({"champions":outgoing}),&json!({"champions":incoming}),data);s[0]*(1+data.champions.values().map(|c|c.slots as i64).sum::<i64>())+s[1]}
fn transfers(previous:&Value,current:&Value,data:&GameData)->Vec<Value>{let old=&previous["item_holders"];let new=&current["item_holders"];let fielded=names(current);let mut out=vec![];for kind in ["tank","ad","ap"]{let profile=|name:&str|data.champions.get(name).map_or("unknown",item_profile);let dest=new[kind].as_str().or_else(||if kind!="tank"{new["flex"].as_str()}else{None}).filter(|name|compatible(profile(name),kind));let mut sources=vec![];if let Some(s)=old[kind].as_str(){sources.push(s)}if kind!="tank"&&(old["flex"]!=new["flex"]||old[kind]!=new[kind]){if let Some(s)=old["flex"].as_str(){if !sources.contains(&s){sources.push(s)}}}for source in sources{if !compatible(profile(source),kind){continue}if let Some(dest)=dest.filter(|x|*x!=source){let retained=fielded.iter().any(|n|n==source);let mut condition=format!("Only if {source} actually holds {} items and {dest} is your chosen holder. ",kind.to_uppercase());if old["flex"].as_str()==Some(source)||new["flex"].as_str()==Some(dest){condition.push_str("Yone/Flex AD and AP paths are alternatives depending on the actual build, not two assumed item sets. ")}condition.push_str(&if retained{format!("{source} remains fielded: use an item remover or wait for a later sale; items do not move freely.")}else{format!("Secure {dest} first, then sell outgoing {source} to release those items.")});out.push(json!({"kind":kind,"outgoing":source,"incoming":dest,"method":if retained{"remover_or_later_sale"}else{"sell_outgoing"},"condition":condition}));}}}out}
pub fn annotate_result(result:&mut Value,data:&GameData)->Result<(),String> {
    let root_level=result["level"].as_u64().or_else(||result["buildup"].as_array().and_then(|steps|steps.last()).and_then(|step|step["level"].as_u64())).unwrap_or_else(||board_level(result,data));
    let root_target=board_identity(result);
    let descriptors=descriptors(data);
    let targets=freeze_targets(result,data,&descriptors,root_level,&root_target);
    let mut previous:Option<Value>=None;
    let mut fallback_previous:Option<Value>=None;
    if let Some(steps)=result["buildup"].as_array_mut() {
        for step in steps {
            let level=step["level"].as_u64().unwrap_or_else(||board_level(&step["board"],data));
            let target=&root_target;
            step["replacements"]=json!([]);
            step["item_transfers"]=json!([]);
            if !step["board"].is_null() {
                let final_board=same_board(&step["board"],target);
                holders(&mut step["board"],&descriptors,previous.as_ref().map(|p|&p["item_holders"]),level,final_board,&targets);
                if let Some(p)=&previous {
                    step["replacements"]=json!(replacement_plan(p,&step["board"],data).1);
                    step["item_transfers"]=json!(transfers(p,&step["board"],data));
                }
            }
            if !step["fallback"].is_null() {
                let final_board=same_board(&step["fallback"],target);
                holders(&mut step["fallback"],&descriptors,fallback_previous.as_ref().map(|p|&p["item_holders"]),level,final_board,&targets);
            }
            if let Some(preps)=step["preparation_boards"].as_array_mut() {
                for p in preps {
                    if !p["board"].is_null(){let final_board=same_board(&p["board"],target);holders(&mut p["board"],&descriptors,None,level,final_board,&targets)}
                }
            }
            let mut phase_previous=if step["board"].is_null(){None}else{Some(step["board"].clone())};
            if let Some(phases)=step["upgrades"].as_array_mut() {
                for phase in phases {
                    phase["replacements"]=json!([]);
                    phase["item_transfers"]=json!([]);
                    if !phase["board"].is_null() {
                        let final_board=same_board(&phase["board"],target);
                        holders(&mut phase["board"],&descriptors,phase_previous.as_ref().map(|p|&p["item_holders"]),level,final_board,&targets);
                        if let Some(p)=&phase_previous {
                            phase["replacements"]=json!(replacement_plan(p,&phase["board"],data).1);
                            phase["item_transfers"]=json!(transfers(p,&phase["board"],data));
                        }
                    }
                    phase_previous=if phase["board"].is_null(){None}else{Some(phase["board"].clone())};
                }
            }
            if !step["goal_board"].is_null() {
                let final_board=same_board(&step["goal_board"],target);
                holders(&mut step["goal_board"],&descriptors,phase_previous.as_ref().map(|p|&p["item_holders"]),level,final_board,&targets);
            }
            previous=phase_previous;
            fallback_previous=if step["fallback"].is_null(){None}else{Some(step["fallback"].clone())};
        }
    }
    holders(result,&descriptors,None,root_level,true,&targets);
    crate::item_builds::annotate_item_builds(result,data)
}

/// Cap-out annotations use one finite physical item budget, never fresh trios
/// for new recipients. Normal guide annotations intentionally remain separate.
pub(crate) fn annotate_cap_transition(from:&mut Value,target:&mut Value,phases:&mut [Value],data:&GameData,items:&crate::cap_items::ItemContext)->Result<(),String> {
    let mut previous=from.clone();
    for phase in phases.iter_mut() {
        phase["replacements"]=json!(replacement_plan(&previous,&phase["board"],data).1);
        previous=phase["board"].clone();
    }
    items.annotate_boards(from,target,phases,data)?;
    crate::positioning::annotate_positioning(from,data)?;
    crate::positioning::annotate_positioning(target,data)?;
    for phase in phases {
        crate::positioning::annotate_positioning(&mut phase["board"],data)?;
    }
    Ok(())
}
