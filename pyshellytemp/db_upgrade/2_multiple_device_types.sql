pragma foreign_keys=off;

create table new_devices (id integer primary key not null, ident text not null unique, type text not null, name text not null, status integer not null, last_temp real null, last_hum real null, last_report real not null, bat_percent real not null);
create table shelly_v1_ht_info (id integer primary key not null, device_id integer not null unique, last_refresh real not null, ip_addr text not null, bat_volt real not null, update_status text not null, wifi_rssi integer not null, mem_total integer not null, mem_free integer not null, fs_size integer not null, fs_free integer not null, temp_thresh real not null, hum_thresh real not null, temp_off real not null, hum_off real not null, need_config_set integer not null, foreign key (device_id) references devices (id) on delete cascade);

insert into new_devices(id, ident, type, name, status, last_temp, last_hum,
		last_report, bat_percent)
	select id, ident, 'shv1ht', name, status, last_temp, last_hum, last_report,
			bat_percent
		from devices;

insert into shelly_v1_ht_info(id, device_id, last_refresh, ip_addr, bat_volt,
		update_status, wifi_rssi, mem_total, mem_free, fs_size, fs_free,
		temp_thresh, hum_thresh, temp_off, hum_off, need_config_set)
	select id, id, last_refresh, ip_addr, bat_volt, update_status, wifi_rssi,
			mem_total, mem_free, fs_size, fs_free, temp_thresh, hum_thresh,
			temp_off, hum_off, need_config_set
		from devices;

drop table devices;
alter table new_devices rename to devices;
pragma foreign_keys=on;

pragma user_version=2;