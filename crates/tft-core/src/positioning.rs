use crate::{guidance::{item_profile,names,planned_unit_strength,visit_boards},model::GameData};
use serde_json::{json,Value};
use std::collections::{BTreeMap,BTreeSet};
const FRONT:[usize;7]=[4,3,2,5,1,6,0];const SIDE:[usize;7]=[5,6,0,4,1,3,2];const RIGHT:[usize;7]=[6,5,4,3,2,1,0];
fn key(n:&str)->(String,&str){(n.to_lowercase(),n)}
struct Layout<'a>{data:&'a GameData,profiles:BTreeMap<String,&'static str>,reserved:BTreeSet<(usize,usize)>,occupied:BTreeSet<(usize,usize)>,markers:Vec<Value>}
impl Layout<'_>{fn place<F>(&mut self,name:&str,cells:&[(usize,usize)],why:F)->Result<(),String>where F:Fn(usize,usize)->String {let(row,column)=cells.iter().copied().find(|c|!self.occupied.contains(c)&&!self.reserved.contains(c)).ok_or("Not enough free hexes to preserve the isolated solo tank's spacing.")?;let c=self.data.champions.get(name);let range=c.and_then(|c|c.attack_range).filter(|r|r.is_finite()&&*r>0.0);let mut details=vec!["Estimated placement".to_owned(),why(row,column),c.filter(|c|!c.role.is_empty()).map(|c|format!("Role: {}",c.role)).unwrap_or("Combat role unavailable".into())];if let Some(r)=range{details.push(format!("Attack range: {r} hexes"));if !c.and_then(|c|c.extra.get("attack_range_source")).and_then(Value::as_str).is_some_and(|s|s.starts_with("https://")){details.push("Attack-range source unavailable".into())}}else{details.push("Attack range unavailable; role-only/conservative fallback, not a sourced range".into())}if self.profiles[name]=="flex"{details.push("Yone AD/AP flex does not change short-range protection".into())}let slots=c.map_or(1,|c|c.slots);if slots!=1{details.push(format!("Uses {slots} team slots; shown once, not as extra champions"))}self.occupied.insert((row,column));self.markers.push(json!({"champion":name,"row":row,"column":column,"reason":details.join("; ")+"."}));Ok(())}}
fn annotate_board(board:&mut Value,data:&GameData)->Result<(),String>{
 let mut ns:Vec<_>=names(board).into_iter().collect::<BTreeSet<_>>().into_iter().collect();
 ns.sort_by(|a,b|key(a).cmp(&key(b)));
 if ns.len()>28{return Err("Positioning supports at most 28 distinct champions on the 4x7 half-board.".into())}
 let profiles:BTreeMap<_,_>=ns.iter().map(|n|(n.clone(),data.champions.get(n).map_or("unknown",item_profile))).collect();
 let mut main=board["item_holders"]["tank"].as_str().map(str::to_owned);
 let(mut tanks,mut close,mut ranged)=(vec![],vec![],vec![]);
 for n in &ns {
  if n=="Teemo"{continue}
  let c=data.champions.get(n);
  if profiles[n]=="tank"{tanks.push(n.clone())}
  else if c.and_then(|c|c.attack_range).is_some_and(|r|r.is_finite()&&r>0.0&&r<=2.0)||c.is_some_and(|c|c.role.to_lowercase().split_whitespace().any(|w|["melee","fighter","assassin"].contains(&w))){close.push(n.clone())}
  else {ranged.push(n.clone())}
 }
 if !tanks.is_empty()&&!main.as_ref().is_some_and(|m|tanks.contains(m)){
  main=tanks.iter().min_by(|a,b|{
   let strength=|n:&String|data.champions.get(n).map_or(0.0,|c|planned_unit_strength(c,board["planned_stars"][n].as_u64().unwrap_or(1) as u8));
   strength(b).total_cmp(&strength(a)).then_with(||key(a).cmp(&key(b)))
  }).cloned();
 }
 tanks.sort_by(|a,b|(Some(a)!=main.as_ref()).cmp(&(Some(b)!=main.as_ref())).then_with(||key(a).cmp(&key(b))));
 let isolated=tanks.len()==1&&main.as_deref()==Some("Amumu");
 let mut layout=Layout{data,profiles,reserved:if isolated{[(0,2),(0,4),(1,2),(1,3)].into_iter().collect()}else{BTreeSet::new()},occupied:BTreeSet::new(),markers:vec![]};
 if ns.iter().any(|n|n=="Teemo"){layout.place("Teemo",&[(3,3)],|_,_|"Teemo large-area spell exception: centered on the back row".into())?}
 let tank_cells:Vec<_>=(0..4).flat_map(|r|FRONT.map(|c|(r,c))).collect();
 for n in &tanks{
  let mut cells=vec![];if isolated&&Some(n)==main.as_ref(){cells.push((0,3))}cells.extend(&tank_cells);
  layout.place(n,&cells,|r,c|{
   if r!=0{return "Front row full; tank overflow behind the frontline".into()}
   let priority=if Some(n)==main.as_ref(){"Main item-holder tank"}else{"Tank"};
   if Some(n)==main.as_ref()&&isolated{return "Isolated solo tank: front center with adjacent friendly hexes kept empty".into()}
   if tanks.len()==1{return "Sole main tank: one hex off center toward the right-side carries".into()}
   if c==0||c==6{format!("{priority}: interior frontline filled, connected extension to an edge")}else{format!("{priority}: compact front-middle formation, adjacent to other tanks")}
  })?;
 }
 let front:BTreeSet<_>=layout.markers.iter().filter(|m|m["row"]==0&&layout.profiles[m["champion"].as_str().unwrap()]=="tank").map(|m|m["column"].as_u64().unwrap() as usize).collect();
 let protected=|c:usize|front.contains(&c)||front.contains(&(c+1));
 let mut columns=SIDE;columns.sort_by_key(|c|(!protected(*c),SIDE.iter().position(|x|x==c).unwrap()));
 let close_cells:Vec<_>=columns.into_iter().map(|c|(1,c)).chain([2,3,0].into_iter().flat_map(|r|SIDE.map(|c|(r,c)))).collect();
 for n in close{layout.place(&n,&close_cells,|r,c|match r{
  1=>if protected(c){"Melee/short-range unit: second-row side placement directly behind an adjacent frontline tank"}else{"Melee/short-range unit: second-row side preference; no adjacent frontline tank available"},
  0=>"Rear rows full; unavoidable frontline overflow for this crowded board",
  _=>"Second row full; melee/short-range overflow behind the frontline"
 }.into())?}
 let ranged_cells:Vec<_>=[3,2,1,0].into_iter().flat_map(|r|RIGHT.map(|c|(r,c))).collect();
 for n in ranged{layout.place(&n,&ranged_cells,|r,_|match r{
  3=>"Ranged or range-unknown unit: backline clustered from the right",
  0=>"Rear rows full; unavoidable frontline overflow for this crowded board",
  _=>"Back row full; right-side backline overflow behind the frontline"
 }.into())?}
 if board["positioning_constraint"].as_str()==Some("one_front_two_rows"){
  if let Some(anchor)=main.as_ref().or(ns.first()){
   layout.occupied.clear();layout.markers.clear();layout.reserved.clear();
   layout.place(anchor,&[(0,4)],|_,_|"Vladimir unlock setup: the only unit in the front two rows".into())?;
   let rear:Vec<_>=[3,2].into_iter().flat_map(|r|RIGHT.map(|c|(r,c))).collect();
   for n in &ns{if n!=anchor{layout.place(n,&rear,|_,_|"Vladimir unlock setup: keep every other unit in the back two rows".into())?}}
   if layout.markers.iter().filter(|m|m["row"].as_u64().unwrap()<2).count()!=1{return Err("Vladimir preparation requires exactly one unit in the front two rows.".into())}
  }
 }
 layout.markers.sort_by_key(|m|{let n=m["champion"].as_str().unwrap();(m["row"].as_u64().unwrap(),layout.profiles[n]!="tank",Some(n)!=main.as_deref(),-(m["column"].as_i64().unwrap()),(n.to_lowercase(),n.to_owned()))});
 board["lineup_order"]=json!(layout.markers.iter().map(|m|m["champion"].clone()).collect::<Vec<_>>());
 board["positioning"]=json!(layout.markers);
 Ok(())
}
pub fn annotate_positioning(result:&mut Value,data:&GameData)->Result<(),String>{visit_boards(result,&mut |b|annotate_board(b,data))}
