--魂の綱
--Soul Rope (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "Activate only by paying 1000 Life Points when a monster you control is
--destroyed and sent to the Graveyard. Special Summon 1 Level 4 monster from your Deck."
--Diferencia con CardScripts (errata moderna): el texto de hoy exige que el monstruo sea destruido 'by a card effect'
--(REASON_EFFECT en cfilter); en 2008 vale cualquier destruccion, tambien la de combate. Como ocgcore pierde el EVENT_TO_GRAVE
--de una destruccion en combate (cae en el Damage Step y las cartas sin EFFECT_FLAG_DAMAGE_STEP no lo ven), la activacion
--se registra tambien con EVENT_BATTLE_DESTROYED (se ofrece al acabar el Damage Step). Lo demas igual.
local s,id=GetID()
function s.initial_effect(c)
	--Activate
	local e1=Effect.CreateEffect(c)
	e1:SetCategory(CATEGORY_SPECIAL_SUMMON)
	e1:SetType(EFFECT_TYPE_ACTIVATE)
	e1:SetCode(EVENT_TO_GRAVE)
	e1:SetCondition(s.condition)
	e1:SetCost(Cost.PayLP(1000))
	e1:SetTarget(s.target)
	e1:SetOperation(s.activate)
	c:RegisterEffect(e1)
	--(2008) tambien cuando el monstruo es destruido en combate
	local e2=e1:Clone()
	e2:SetCode(EVENT_BATTLE_DESTROYED)
	c:RegisterEffect(e2)
end
function s.cfilter(c,tp)
	return c:IsReason(REASON_DESTROY) and c:IsMonster()
		and c:IsPreviousControler(tp) and c:IsPreviousLocation(LOCATION_MZONE)
end
function s.condition(e,tp,eg,ep,ev,re,r,rp)
	return eg:IsExists(s.cfilter,1,nil,tp)
end
function s.spfilter(c,e,tp)
	return c:GetLevel()==4 and c:IsCanBeSpecialSummoned(e,0,tp,false,false)
end
function s.target(e,tp,eg,ep,ev,re,r,rp,chk)
	if chk==0 then return Duel.GetLocationCount(tp,LOCATION_MZONE)>0
		and Duel.IsExistingMatchingCard(s.spfilter,tp,LOCATION_DECK,0,1,nil,e,tp) end
	Duel.SetOperationInfo(0,CATEGORY_SPECIAL_SUMMON,nil,1,tp,LOCATION_DECK)
end
function s.activate(e,tp,eg,ep,ev,re,r,rp)
	if Duel.GetLocationCount(tp,LOCATION_MZONE)<=0 then return end
	Duel.Hint(HINT_SELECTMSG,tp,HINTMSG_SPSUMMON)
	local g=Duel.SelectMatchingCard(tp,s.spfilter,tp,LOCATION_DECK,0,1,1,nil,e,tp)
	if #g>0 then
		Duel.SpecialSummon(g,0,tp,tp,false,false,POS_FACEUP)
	end
end