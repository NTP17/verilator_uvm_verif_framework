// ==========================================================================
//  VPI-based signal driver for Verilator
//  Provides a DPI-C callable function that writes to RTL signals via VPI,
//  bypassing virtual-interface scheduling limitations in Verilator.
//
//  Verilator does not propagate value changes written to virtual-interface
//  members from class code through port connections to DUT internal signals.
//  This driver writes directly to the RTL signals AND propagates to any
//  connected module ports discovered via VPI load iteration.
// ==========================================================================
#include "svdpi.h"
#include "vpi_user.h"
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

extern "C" {

// Cache VPI handles to avoid repeated lookups
static std::unordered_map<std::string, vpiHandle> handle_cache;

// Cache port propagation targets: for each interface signal, the list of
// connected DUT/module port handles that need the same value.
static std::unordered_map<std::string, std::vector<vpiHandle>> propagation_cache;
static bool propagation_initialized = false;

// Build propagation map by scanning all module instances in the design.
// For each interface signal, find connected module input ports.
static void init_propagation() {
    if (propagation_initialized) return;
    propagation_initialized = true;

    // Iterate over all modules in the design
    vpiHandle top_iter = vpi_iterate(vpiModule, nullptr);
    if (!top_iter) return;

    vpiHandle top;
    while ((top = vpi_scan(top_iter)) != nullptr) {
        // For each top module, iterate sub-modules
        vpiHandle mod_iter = vpi_iterate(vpiModule, top);
        if (!mod_iter) continue;
        vpiHandle mod;
        while ((mod = vpi_scan(mod_iter)) != nullptr) {
            // Check if this is an interface (has no ports of its own that
            // are module instances) — we look for modules with ports
            vpiHandle port_iter = vpi_iterate(vpiPort, mod);
            if (!port_iter) continue;
            // This module has ports — check each port's high connection
            vpiHandle port;
            while ((port = vpi_scan(port_iter)) != nullptr) {
                // Get the high-conn (the expression connected to this port)
                vpiHandle highconn = vpi_handle(vpiHighConn, port);
                if (!highconn) continue;
                const char* hc_name = vpi_get_str(vpiFullName, highconn);
                const char* port_name = vpi_get_str(vpiFullName, port);
                if (hc_name && port_name) {
                    // If the high-conn is an interface signal, register
                    // this port as a propagation target
                    std::string key(hc_name);
                    vpiHandle port_handle = vpi_handle_by_name(
                        const_cast<char*>(port_name), nullptr);
                    if (port_handle) {
                        propagation_cache[key].push_back(port_handle);
                    }
                }
            }
        }
    }
}

void svpp_vpi_drive(const char* signal_path, int value, int width) {
    std::string key(signal_path);
    vpiHandle h;

    auto it = handle_cache.find(key);
    if (it != handle_cache.end()) {
        h = it->second;
    } else {
        h = vpi_handle_by_name(const_cast<char*>(signal_path), nullptr);
        if (!h) return;  // signal not found — silently skip
        handle_cache[key] = h;
    }

    s_vpi_value val;
    val.format = vpiIntVal;
    val.value.integer = value;
    vpi_put_value(h, &val, nullptr, vpiNoDelay);

    // Propagate to connected module ports
    init_propagation();
    auto prop_it = propagation_cache.find(key);
    if (prop_it != propagation_cache.end()) {
        for (vpiHandle ph : prop_it->second) {
            vpi_put_value(ph, &val, nullptr, vpiNoDelay);
        }
    }
}

}  // extern "C"
