pragma foreign_keys=off;

create table new_shelly_v1_ht_info (id integer primary key not null, device_id integer not null unique, username text not null, password text not null, last_refresh real null, ip_addr text not null, bat_volt real not null, update_status text not null, wifi_rssi integer not null, mem_total integer not null, mem_free integer not null, fs_size integer not null, fs_free integer not null, temp_thresh real not null, hum_thresh real not null, temp_off real not null, hum_off real not null, need_config_set integer not null, foreign key (device_id) references devices (id) on delete cascade);

insert into new_shelly_v1_ht_info(id, device_id, username, password,
		last_refresh, ip_addr, bat_volt, update_status, wifi_rssi, mem_total,
		mem_free, fs_size, fs_free, temp_thresh, hum_thresh, temp_off, hum_off,
		need_config_set)
	select i.id, i.device_id, s.dev_username, s.dev_password, i.last_refresh,
		i.ip_addr, i.bat_volt, i.update_status, i.wifi_rssi, i.mem_total,
		i.mem_free, i.fs_size, i.fs_free, i.temp_thresh, i.hum_thresh,
		i.temp_off, i.hum_off, i.need_config_set
		from shelly_v1_ht_info i, settings s;

drop table shelly_v1_ht_info;
alter table new_shelly_v1_ht_info rename to shelly_v1_ht_info;

drop table settings;

pragma foreign_keys=on;

pragma user_version=3;
