create table dev_identify (id integer primary key not null, identify text not null, until real not null);

insert into dev_identify (id, identify, until)
	select 1, dev_identify, identify_until from settings;

alter table settings drop column dev_identify;
alter table settings drop column identify_until;

pragma user_version=1;