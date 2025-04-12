pragma foreign_keys=off;

create table new_devices (id integer primary key not null, ident text not null unique, type text not null, name text not null, temp_color text not null, hum_color text not null, status integer not null, last_temp real null, last_hum real null, last_report real not null, bat_percent real not null);
create temp table colors (temp text not null unique, hum text not null unique);
insert into colors values
	('#CC0000', '#0100CC'),
	('#CC6700', '#6600CC'),
	('#CC0067', '#0067CC'),
	('#CC3200', '#3300CC'),
	('#CC0033', '#0010CC');

-- Try to assign a different color for each device
insert into new_devices (id, ident, type, name, temp_color, hum_color, status, last_temp, last_hum, last_report, bat_percent)
	select d.id, d.ident, d.type, d.name, c.temp, c.hum, d.status, d.last_temp, d.last_hum, d.last_report, d.bat_percent
		from devices d, colors c
		where (d.id % 5) + 1 = c.rowid;

drop table colors;
drop table devices;
alter table new_devices rename to devices;
pragma foreign_keys=on;

pragma user_version=5;