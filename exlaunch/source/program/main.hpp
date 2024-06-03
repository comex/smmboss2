#pragma once
#include <array>
#include <optional>
#include <stdint.h>

using BuildId = std::array<uint8_t, 16>;

#include <lib/util/sys/mem_layout.hpp>

extern std::array<std::optional<BuildId>, exl::util::mem_layout::s_MaxModules> g_build_ids;
void note_new_hose_connection();
