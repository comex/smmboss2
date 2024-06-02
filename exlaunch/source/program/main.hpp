#pragma once
#include "generated.hpp"

#include <atomic>
#include <array>
#include <optional>

#include <lib/util/sys/mem_layout.hpp>

extern std::atomic<uint64_t> g_hash_tweak;
extern std::array<std::optional<BuildId>, exl::util::mem_layout::s_MaxModules> g_build_ids;
