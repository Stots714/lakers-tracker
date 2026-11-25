Games final on both sides: 258

## Side-by-side check: GitHub (cdn.nba.com) against the PC (stats.nba.com)

| File | Rows on both | Only GitHub | Only PC | Rows with any difference | Columns that differ |
|---|---|---|---|---|---|
| league_results.csv | 258 | 0 | 0 | 0 | none |
| league_box.csv | 516 | 0 | 0 | 12 | fga (6), tov (4), tov_players (4), fg3a (3), dreb (3), oreb (3) |
| league_player_games.csv | 5772 | 0 | 0 | 108 | min (28), stl (18), ast (15), dreb (15), tov (12), fga (11), oreb (9), blk (6), fg3a (3), pts (2), fgm (2), player (2) |
| league_quarters.csv | 516 | 0 | 0 | 0 | none |
| league_inactive.csv | 803 | 0 | 1326 | 0 | none |
| league_extras.csv | 516 | 0 | 0 | 116 | biggest_run (68), pts_second_chance (15), pts_off_tov (15), pts_paint (15), pts_fast_break (5), times_tied (2), tov_total (2), reb_team (2), biggest_lead (2) |

### Examples (GitHub value / PC value)

- league_box.csv fga: 0022600091/DAL: '79' / '80'; 0022600109/DAL: '88' / '87'; 0022600118/MEM: '95' / '96'; 0022600118/SAC: '94' / '95'
- league_box.csv tov: 0022600003/OKC: '14' / '13'; 0022600015/CHA: '12' / '13'; 0022600118/SAC: '14' / '13'; 0022600204/CHA: '17' / '16'
- league_box.csv tov_players: 0022600003/OKC: '11' / '10'; 0022600015/CHA: '10' / '11'; 0022600118/SAC: '14' / '13'; 0022600204/CHA: '14' / '13'
- league_box.csv fg3a: 0022600091/DAL: '37' / '38'; 0022600118/MEM: '40' / '41'; 0022600168/UTA: '34' / '33'
- league_box.csv dreb: 0022600102/LAC: '23' / '24'; 0022600118/MEM: '36' / '37'; 0022600208/HOU: '36' / '35'
- league_box.csv oreb: 0022600118/MEM: '18' / '19'; 0022600200/BOS: '20' / '19'; 0022600209/SAS: '15' / '14'
- league_player_games.csv min: 0022600002/1628973: '34.08' / '34.1'; 0022600009/1628368: '33.7' / '32.4'; 0022600009/1628436: '16.02' / '15.88'; 0022600009/1630170: '35.52' / '33.55'
- league_player_games.csv stl: 0022600007/1631114: '0' / '1'; 0022600007/1642349: '5' / '4'; 0022600085/1628976: '1' / '2'; 0022600085/1630217: '3' / '2'
- league_player_games.csv ast: 0022600007/1628963: '1' / '2'; 0022600007/1643813: '3' / '2'; 0022600009/201142: '6' / '5'; 0022600094/1630700: '4' / '5'
- league_player_games.csv dreb: 0022600007/1631119: '7' / '6'; 0022600007/1641717: '1' / '2'; 0022600092/1631123: '2' / '1'; 0022600092/1642346: '1' / '2'
- league_player_games.csv tov: 0022600003/1628983: '2' / '1'; 0022600015/1642275: '0' / '1'; 0022600018/1626157: '2' / '3'; 0022600018/1628969: '1' / '0'
- league_player_games.csv fga: 0022600091/1630230: '13' / '14'; 0022600109/1642383: '3' / '2'; 0022600118/1627734: '8' / '9'; 0022600118/1642907: '11' / '13'
- league_player_games.csv oreb: 0022600007/1641747: '1' / '0'; 0022600007/1643624: '1' / '2'; 0022600118/1642377: '2' / '3'; 0022600125/1627832: '1' / '0'
- league_player_games.csv blk: 0022600117/1630194: '3' / '2'; 0022600118/203924: '2' / '3'; 0022600195/1641705: '1' / '2'; 0022600200/1628398: '2' / '1'
- league_player_games.csv fg3a: 0022600091/1630230: '6' / '7'; 0022600118/1643409: '7' / '8'; 0022600168/1641729: '6' / '5'
- league_player_games.csv pts: 0022600118/1642907: '15' / '17'; 0022600118/1643409: '16' / '14'
- league_player_games.csv fgm: 0022600118/1642907: '6' / '7'; 0022600118/1643409: '6' / '5'
- league_player_games.csv player: 0022600231/1629661: 'Cam Johnson' / 'Cameron Johnson'; 0022600232/1629023: 'P.J. Washington Jr.' / 'P.J. Washington'
- league_inactive.csv only on the PC, e.g. 0022600001/1627752, 0022600001/1641759, 0022600001/1641989
- league_extras.csv biggest_run: 0022600006/PHI: '15' / '16.0'; 0022600007/OKC: '7' / '8.0'; 0022600015/CHA: '6' / '7.0'; 0022600016/CHI: '6' / '8.0'
- league_extras.csv pts_second_chance: 0022600009/HOU: '8' / '10.0'; 0022600021/DAL: '6' / '9.0'; 0022600028/DAL: '20' / '21.0'; 0022600049/UTA: '24' / '25.0'
- league_extras.csv pts_off_tov: 0022600015/CHA: '10' / '8.0'; 0022600049/UTA: '11' / '9.0'; 0022600102/LAL: '33' / '31.0'; 0022600115/TOR: '19' / '20.0'
- league_extras.csv pts_paint: 0022600027/PHI: '30' / '32.0'; 0022600088/TOR: '58' / '60.0'; 0022600096/ORL: '54' / '56.0'; 0022600113/BKN: '40' / '42.0'
- league_extras.csv pts_fast_break: 0022600163/PHI: '31' / '29.0'; 0022600217/IND: '15' / '17.0'; 0022600217/WAS: '11' / '9.0'; 0022600219/BOS: '6' / '9.0'
- league_extras.csv times_tied: 0022600044/BKN: '6' / '5.0'; 0022600044/ORL: '6' / '5.0'
- league_extras.csv tov_total: 0022600049/PHX: '10' / '9.0'; 0022600118/SAC: '14' / '13.0'
- league_extras.csv reb_team: 0022600091/DAL: '11' / '12.0'; 0022600109/DAL: '8' / '7.0'
- league_extras.csv biggest_lead: 0022600189/DEN: '27' / '0.0'; 0022600189/IND: '5' / '0.0'
