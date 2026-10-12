<!--
  Copyright (C) 2022 Nethesis S.r.l.
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("settings.title") }}</h2>
      </cv-column>
    </cv-row>
    <cv-row v-if="error.getConfiguration">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-configuration')"
          :description="error.getConfiguration"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <!-- addresses and admin login -->
    <cv-row v-if="configured">
      <cv-column>
        <cv-tile light>
          <h4 class="mg-bottom">{{ $t("settings.access_title") }}</h4>
          <cv-text-input
            id="homeserver-url"
            :label="$t('settings.homeserver_url')"
            :helper-text="$t('settings.homeserver_url_helper')"
            :value="homeserverUrl"
            readonly
            class="mg-bottom"
          />
          <cv-text-input
            id="admin-user"
            :label="$t('settings.admin_user')"
            :value="adminUser"
            readonly
            class="mg-bottom"
          />
          <cv-text-input
            v-if="adminPassword"
            id="admin-password"
            :label="$t('settings.admin_password')"
            :helper-text="$t('settings.admin_password_helper')"
            :value="adminPassword"
            type="password"
            readonly
            class="mg-bottom"
          />
          <div class="buttons">
            <NsButton
              v-for="app in openableApps"
              :key="app.id"
              kind="secondary"
              :icon="Launch20"
              @click="openUrl(app.url)"
              >{{ $t("settings.open_" + app.id) }}</NsButton
            >
          </div>
          <template v-if="wellknownServer">
            <h5 class="mg-top mg-bottom-sm">
              {{ $t("settings.delegation_title") }}
            </h5>
            <p class="mg-bottom helper">
              {{
                $t("settings.delegation_description", {
                  server_name: serverName,
                })
              }}
            </p>
            <cv-text-input
              id="wellknown-server"
              :label="'https://' + serverName + '/.well-known/matrix/server'"
              :value="wellknownServer"
              readonly
              class="mg-bottom"
            />
            <cv-text-input
              id="wellknown-client"
              :label="'https://' + serverName + '/.well-known/matrix/client'"
              :value="wellknownClient"
              readonly
              class="mg-bottom"
            />
          </template>
        </cv-tile>
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <cv-form @submit.prevent="configureModule">
            <!-- homeserver -->
            <h4 class="mg-bottom">{{ $t("settings.homeserver_title") }}</h4>
            <cv-text-input
              id="matrix-host"
              :label="$t('settings.host')"
              :helper-text="$t('settings.host_helper')"
              placeholder="matrix.example.org"
              v-model.trim="host"
              class="mg-bottom"
              :invalid-message="$t(error.host)"
              :disabled="busy"
              ref="host"
            />
            <cv-text-input
              id="server-name"
              :label="$t('settings.server_name')"
              :helper-text="
                configured
                  ? $t('settings.server_name_fixed')
                  : $t('settings.server_name_helper')
              "
              placeholder="example.org"
              v-model.trim="serverName"
              class="mg-bottom"
              :invalid-message="$t(error.server_name)"
              :disabled="busy || configured"
              ref="server_name"
            />
            <cv-toggle
              v-if="isDelegated"
              value="wellknownOnServerName"
              :label="$t('settings.wellknown_on_server_name')"
              v-model="wellknownOnServerName"
              :disabled="busy"
              class="mg-bottom"
            >
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </cv-toggle>
            <p v-if="isDelegated" class="mg-bottom helper">
              {{ $t("settings.wellknown_helper", { server_name: serverName }) }}
            </p>
            <cv-toggle
              value="letsEncrypt"
              :label="$t('settings.lets_encrypt')"
              v-model="isLetsEncryptEnabled"
              :disabled="busy"
              class="mg-bottom"
            >
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </cv-toggle>
            <cv-toggle
              value="httpToHttps"
              :label="$t('settings.http_to_https')"
              v-model="isHttpToHttpsEnabled"
              :disabled="busy"
              class="mg-bottom"
            >
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </cv-toggle>
            <cv-toggle
              value="federation"
              :label="$t('settings.federation')"
              v-model="federation"
              :disabled="busy"
              class="mg-bottom"
            >
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </cv-toggle>
            <!-- accounts -->
            <h4 class="mg-top mg-bottom-sm">
              {{ $t("settings.accounts_title") }}
            </h4>
            <p class="mg-bottom helper">
              {{ $t("settings.accounts_description") }}
            </p>
            <cv-select
              id="user-domain"
              :label="$t('settings.user_domain')"
              v-model="userDomain"
              :invalid-message="$t(error.user_domain)"
              :disabled="busy"
              class="mg-bottom"
              ref="user_domain"
            >
              <cv-select-option value="">{{
                $t("settings.local_accounts_only")
              }}</cv-select-option>
              <cv-select-option
                v-for="domain in userDomains"
                :key="domain"
                :value="domain"
                >{{ domain }}</cv-select-option
              >
            </cv-select>
            <!-- web apps -->
            <h4 class="mg-top mg-bottom-sm">
              {{ $t("settings.web_apps_title") }}
            </h4>
            <p class="mg-bottom helper">
              {{ $t("settings.web_apps_description") }}
            </p>
            <cv-text-input
              v-for="app in webApps"
              :key="app"
              :id="app + '-host'"
              :label="$t('settings.' + app + '_host')"
              :placeholder="$t('settings.' + app + '_placeholder')"
              v-model.trim="appHosts[app]"
              class="mg-bottom"
              :invalid-message="$t(error[app + '_host'])"
              :disabled="busy"
              :ref="app + '_host'"
            />
            <!-- bridges -->
            <h4 class="mg-top mg-bottom-sm">
              {{ $t("settings.bridges_title") }}
            </h4>
            <p class="mg-bottom helper">
              {{ $t("settings.bridges_description") }}
            </p>
            <div class="bridges mg-bottom">
              <cv-checkbox
                v-for="bridge in availableBridges"
                :key="bridge.id"
                :id="'bridge-' + bridge.id"
                :value="bridge.id"
                :label="bridge.name"
                v-model="bridgeEnabled[bridge.id]"
                :disabled="busy"
              />
            </div>
            <NsInlineNotification
              v-if="error.bridges"
              kind="error"
              :title="$t('settings.' + error.bridges)"
              :showCloseButton="false"
            />
            <template v-if="bridgeEnabled.telegram">
              <p class="mg-bottom helper">
                {{ $t("settings.telegram_description") }}
              </p>
              <cv-text-input
                id="telegram-api-id"
                :label="$t('settings.telegram_api_id')"
                v-model.trim="telegramApiId"
                class="mg-bottom"
                :invalid-message="$t(error.telegram_api_id)"
                :disabled="busy"
                ref="telegram_api_id"
              />
              <cv-text-input
                id="telegram-api-hash"
                :label="$t('settings.telegram_api_hash')"
                :helper-text="
                  telegramApiHashSet ? $t('settings.telegram_api_hash_set') : ''
                "
                v-model.trim="telegramApiHash"
                type="password"
                autocomplete="off"
                class="mg-bottom"
                :invalid-message="$t(error.telegram_api_hash)"
                :disabled="busy"
                ref="telegram_api_hash"
              />
            </template>
            <!-- advanced -->
            <cv-accordion class="mg-bottom">
              <cv-accordion-item :open="toggleAccordion[0]">
                <template slot="title">{{ $t("settings.advanced") }}</template>
                <template slot="content">
                  <cv-text-input
                    id="mail-from"
                    :label="$t('settings.mail_from')"
                    :helper-text="$t('settings.mail_from_helper')"
                    placeholder="matrix@example.org"
                    v-model.trim="mailFrom"
                    class="mg-bottom"
                    :invalid-message="$t(error.mail_from)"
                    :disabled="busy"
                    ref="mail_from"
                  />
                  <cv-number-input
                    id="max-upload-mb"
                    :label="$t('settings.max_upload_mb')"
                    v-model="maxUploadMb"
                    :min="1"
                    :max="10240"
                    :invalid-message="$t(error.max_upload_mb)"
                    :disabled="busy"
                    ref="max_upload_mb"
                  />
                </template>
              </cv-accordion-item>
            </cv-accordion>
            <cv-row v-if="error.configureModule">
              <cv-column>
                <NsInlineNotification
                  kind="error"
                  :title="$t('action.configure-module')"
                  :description="error.configureModule"
                  :showCloseButton="false"
                />
              </cv-column>
            </cv-row>
            <NsButton
              kind="primary"
              :icon="Save20"
              :loading="loading.configureModule"
              :disabled="busy"
              >{{ $t("settings.save") }}</NsButton
            >
          </cv-form>
        </cv-tile>
      </cv-column>
    </cv-row>
  </cv-grid>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import {
  QueryParamService,
  UtilService,
  TaskService,
  IconService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";

const WEB_APPS = ["element", "cinny", "ketesa"];

export default {
  name: "Settings",
  mixins: [
    TaskService,
    IconService,
    UtilService,
    QueryParamService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("settings.title") + " - " + this.appName;
  },
  data() {
    return {
      q: {
        page: "settings",
      },
      urlCheckInterval: null,
      webApps: WEB_APPS,
      configured: false,
      host: "",
      serverName: "",
      wellknownOnServerName: false,
      isLetsEncryptEnabled: false,
      isHttpToHttpsEnabled: true,
      federation: true,
      userDomain: "",
      userDomains: [],
      appHosts: { element: "", cinny: "", ketesa: "" },
      availableBridges: [],
      bridgeEnabled: {},
      telegramApiId: "",
      telegramApiHash: "",
      telegramApiHashSet: false,
      mailFrom: "",
      maxUploadMb: 100,
      toggleAccordion: [false],
      homeserverUrl: "",
      appUrls: {},
      adminUser: "",
      adminPassword: "",
      wellknownServer: "",
      wellknownClient: "",
      loading: {
        getConfiguration: false,
        configureModule: false,
      },
      error: {
        getConfiguration: "",
        configureModule: "",
        host: "",
        server_name: "",
        user_domain: "",
        element_host: "",
        cinny_host: "",
        ketesa_host: "",
        bridges: "",
        telegram_api_id: "",
        telegram_api_hash: "",
        mail_from: "",
        max_upload_mb: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    busy() {
      return this.loading.getConfiguration || this.loading.configureModule;
    },
    isDelegated() {
      return !!this.serverName && this.serverName !== this.host;
    },
    openableApps() {
      return WEB_APPS.filter((app) => this.appUrls[app]).map((app) => ({
        id: app,
        url: this.appUrls[app],
      }));
    },
  },
  created() {
    this.getConfiguration();
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      vm.watchQueryData(vm);
      vm.urlCheckInterval = vm.initUrlBindingForApp(vm, vm.q.page);
    });
  },
  beforeRouteLeave(to, from, next) {
    clearInterval(this.urlCheckInterval);
    next();
  },
  methods: {
    // Start a module task and route its events to the given handlers
    async runTask(action, data, handlers, extra) {
      const eventId = this.getUuid();
      this.core.$root.$once(`${action}-aborted-${eventId}`, handlers.aborted);
      this.core.$root.$once(
        `${action}-completed-${eventId}`,
        handlers.completed
      );
      if (handlers.validationFailed) {
        this.core.$root.$once(
          `${action}-validation-failed-${eventId}`,
          handlers.validationFailed
        );
      }
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action,
          data,
          extra: {
            title: this.$t("action." + action),
            isNotificationHidden: true,
            ...extra,
            eventId,
          },
        })
      );
      return res[0];
    },
    focusElement(ref) {
      const element = Array.isArray(this.$refs[ref])
        ? this.$refs[ref][0]
        : this.$refs[ref];
      if (element && element.focus) {
        element.focus();
      }
    },
    openUrl(url) {
      window.open(url, "_blank", "noopener");
    },
    async getConfiguration() {
      this.loading.getConfiguration = true;
      this.error.getConfiguration = "";
      const err = await this.runTask("get-configuration", undefined, {
        aborted: this.getConfigurationAborted,
        completed: this.getConfigurationCompleted,
      });
      if (err) {
        console.error("error creating task get-configuration", err);
        this.error.getConfiguration = this.getErrorMessage(err);
        this.loading.getConfiguration = false;
      }
    },
    getConfigurationAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getConfiguration = this.$t("error.generic_error");
      this.loading.getConfiguration = false;
    },
    getConfigurationCompleted(taskContext, taskResult) {
      const config = taskResult.output;
      this.configured = config.configured;
      this.host = config.host;
      this.serverName = config.server_name;
      this.wellknownOnServerName = config.wellknown_on_server_name;
      this.isLetsEncryptEnabled = config.lets_encrypt;
      this.isHttpToHttpsEnabled = config.http2https;
      this.federation = config.federation;
      this.userDomains = config.user_domains;
      this.userDomain = config.user_domain;
      this.appHosts = {
        element: config.element_host,
        cinny: config.cinny_host,
        ketesa: config.ketesa_host,
      };
      this.availableBridges = config.available_bridges;
      const enabled = {};
      for (const bridge of config.available_bridges) {
        enabled[bridge.id] = config.bridges.includes(bridge.id);
      }
      this.bridgeEnabled = enabled;
      this.telegramApiId = config.telegram_api_id;
      this.telegramApiHash = "";
      this.telegramApiHashSet = config.telegram_api_hash_set;
      this.mailFrom = config.mail_from;
      this.maxUploadMb = config.max_upload_mb;
      this.homeserverUrl = config.homeserver_url;
      this.appUrls = {
        element: config.element_url,
        cinny: config.cinny_url,
        ketesa: config.ketesa_url,
      };
      this.adminUser = config.admin_user;
      this.adminPassword = config.admin_password;
      this.wellknownServer = config.wellknown_server;
      this.wellknownClient = config.wellknown_client;
      this.loading.getConfiguration = false;
      this.$nextTick(() => this.focusElement("host"));
    },
    validateConfigureModule() {
      this.clearErrors(this);
      let firstInvalid = "";
      const invalid = (field, error) => {
        this.error[field] = error;
        firstInvalid = firstInvalid || field;
      };
      if (!this.host) {
        invalid("host", "common.required");
      }
      if (this.bridgeEnabled.telegram) {
        if (!this.telegramApiId) {
          invalid("telegram_api_id", "common.required");
        }
        if (!this.telegramApiHash && !this.telegramApiHashSet) {
          invalid("telegram_api_hash", "common.required");
        }
      }
      if (firstInvalid) {
        this.$nextTick(() => this.focusElement(firstInvalid));
        return false;
      }
      return true;
    },
    configureModuleValidationFailed(validationErrors) {
      this.loading.configureModule = false;
      let firstInvalid = "";
      for (const validationError of validationErrors) {
        const param = validationError.parameter;
        this.error[param] = "settings." + validationError.error;
        firstInvalid = firstInvalid || param;
      }
      if (["mail_from", "max_upload_mb"].includes(firstInvalid)) {
        this.toggleAccordion = [true];
      }
      if (firstInvalid) {
        this.$nextTick(() => this.focusElement(firstInvalid));
      }
    },
    async configureModule() {
      if (!this.validateConfigureModule()) {
        return;
      }
      this.loading.configureModule = true;
      const data = {
        host: this.host,
        server_name: this.serverName,
        wellknown_on_server_name: this.wellknownOnServerName,
        lets_encrypt: this.isLetsEncryptEnabled,
        http2https: this.isHttpToHttpsEnabled,
        federation: this.federation,
        user_domain: this.userDomain,
        element_host: this.appHosts.element,
        cinny_host: this.appHosts.cinny,
        ketesa_host: this.appHosts.ketesa,
        bridges: this.availableBridges
          .map((bridge) => bridge.id)
          .filter((id) => this.bridgeEnabled[id]),
        telegram_api_id: this.telegramApiId,
        telegram_api_hash: this.telegramApiHash,
        mail_from: this.mailFrom,
        max_upload_mb: parseInt(this.maxUploadMb, 10),
      };
      const err = await this.runTask(
        "configure-module",
        data,
        {
          aborted: this.configureModuleAborted,
          completed: this.configureModuleCompleted,
          validationFailed: this.configureModuleValidationFailed,
        },
        {
          title: this.$t("settings.instance_configuration", {
            instance: this.instanceName,
          }),
          description: this.$t("settings.configuring"),
          isNotificationHidden: false,
        }
      );
      if (err) {
        console.error("error creating task configure-module", err);
        this.error.configureModule = this.getErrorMessage(err);
        this.loading.configureModule = false;
      }
    },
    configureModuleAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.configureModule = this.$t("error.generic_error");
      this.loading.configureModule = false;
    },
    configureModuleCompleted() {
      this.loading.configureModule = false;
      this.getConfiguration();
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.mg-bottom {
  margin-bottom: $spacing-06;
}

.mg-bottom-sm {
  margin-bottom: $spacing-03;
}

.mg-top {
  margin-top: $spacing-07;
}

.helper {
  max-width: 38rem;
}

.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-05;
}

.bridges {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  gap: $spacing-03;
}
</style>
