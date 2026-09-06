.PHONY: doctor test deploy status logs stop

doctor:
	./scripts/doctor.sh

test:
	./scripts/test.sh

deploy:
	./scripts/deploy.sh

status:
	./scripts/status.sh

logs:
	./scripts/logs.sh

stop:
	./scripts/stop.sh
