#pragma once
#include <array>
#include <optional>
#include <stdint.h>

using BuildId = std::array<uint8_t, 16>;

#include <lib/util/module_index.hpp>

extern std::array<std::optional<BuildId>, static_cast<size_t>(exl::util::ModuleIndex::End)> g_build_ids;
void note_new_hose_connection();
