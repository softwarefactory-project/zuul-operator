image:
	podman build -f build/Dockerfile -t docker.io/zuul/zuul-operator .

install:
	kubectl apply -f deploy/crds/zuul-ci_v1alpha2_zuul_crd.yaml -f deploy/rbac-admin.yaml -f deploy/operator.yaml

deploy-cr:
	kubectl apply -f deploy/crds/zuul-ci_v1alpha2_zuul_cr.yaml

.PHONY: manifests
manifests: controller-gen ## Generate WebhookConfiguration, ClusterRole and CustomResourceDefinition objects.
	$(CONTROLLER_GEN) rbac:roleName=manager-role crd webhook paths="./..." output:crd:artifacts:config=deploy/crds

##@ Build Dependencies
LOCALBIN ?= $(shell pwd)/bin
$(LOCALBIN):
	mkdir -p $(LOCALBIN)
CONTROLLER_GEN ?= $(LOCALBIN)/controller-gen
CONTROLLER_TOOLS_VERSION ?= v0.8.0

.PHONY: controller-gen
controller-gen: $(CONTROLLER_GEN) ## Download controller-gen locally if necessary.
$(CONTROLLER_GEN): $(LOCALBIN)
	GOBIN=$(LOCALBIN) go install sigs.k8s.io/controller-tools/cmd/controller-gen@$(CONTROLLER_TOOLS_VERSION)
