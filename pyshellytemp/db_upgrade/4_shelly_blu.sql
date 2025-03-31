create table shelly_blu_ht_info (id integer primary key not null, device_id integer not null unique, mac_addr text not null, fw_ver text not null, rssi integer not null, enc_key text not null, foreign key (device_id) references devices (id) on delete cascade);

pragma user_version=4;